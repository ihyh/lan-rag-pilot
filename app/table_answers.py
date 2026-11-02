"""表格参数题的确定性回答（实验，默认关闭）。

真实问题诊断里，模型看到正确的 xlsx 行仍会把“行号 18”当成“索引 20”，或把 Unit/Mark 列
的单位说反。这类题的关系可由原文结构确定：行号、列字母、表头名称、单元格值都在检索片段里。
本模块只做**可逐字核对**的表格查找：

- 从 xlsx 检索片段解析 `工作表《Sheet》第 N 行：A18=20；B18=...；` 行与表头行；
- 问题点名了字段名（Name 列匹配）或 `P<数字>`（对应 Index 列值）时，定位目标行；
- 按问题问到的属性（索引/默认值/字段说明/单位/现场当前值）输出列名=值的直接回答，
  明确行号 ≠ 索引，并区分 Unit 与 Mark 列；
- 资料只有定义、没有实测值时，直接说明“无法确认现场当前值”，不用定义冒充现场值；
- 未命中目标字段时返回 None，交回原生成路径。
- 表头必须来自检索上下文中同文档、同工作表的明确列名，不按默认列序猜测。
- 已出现目标文字但表头/坐标/名称与P号无法绑定时，返回澄清，不继续让模型猜列。
- 单元格坐标 P20 不证明参数 P20；指定文件未提供绑定依据时不借其它文件代答。
- 明确 Current Value 列只作为文档记录回答；裸 P 号的数字索引导航不证明参数别名或设备实时状态。

原生 DOCX 表格只读取显式表号/行号/坐标，不从旧扁平片段猜回行列。
不调用模型；输出仍由服务端可核对的原文单元格拼成。
"""
from __future__ import annotations

import re

ROW_LINE = re.compile(r"^(?:工作表|文档表格)《([^》]+)》第\s*(\d+)\s*行：(.*)$")
CELL = re.compile(r"(?:^|[；;])\s*([A-Z]+)(\d+)=(.*?)(?=[；;]\s*[A-Z]+\d+=|$)", re.DOTALL)
PX = re.compile(r"(?<![A-Za-z0-9_.-])P\s*(\d{1,3})(?![A-Za-z0-9_.-])", re.IGNORECASE)
COLUMN_LABELS = {
    "index": {"index", "索引"}, "name": {"name", "名称"}, "value": {"value"},
    "default": {"default", "default value", "defaultvalue", "默认", "默认值", "默認", "默認值"},
    "unit": {"unit", "单位"}, "description": {"description", "描述", "说明"},
    "mark": {"mark", "备注"},
    "current": {"current value", "currentvalue", "actual value", "actualvalue",
                "当前值", "实际值", "实测值", "读取值", "现场当前值"},
}
ATTRIBUTE_WORDS = {
    "index": ("索引", "index"),
    "value": ("value",),
    "default": ("默认", "默認", "default"),
    "description": ("字段说明", "描述", "含义", "description"),
    "unit": ("单位", "unit"),
    "current": ("现场当前值", "当前值", "现场值", "实测值", "实际值", "读取值"),
    "usage": ("怎么用", "怎么使用", "如何使用", "如何设置", "怎么设置", "如何配置", "怎么配置"),
}


def _answer_result(answer: str, model: str = "table_answers") -> dict:
    return {"answer": answer, "model": model, "latency_ms": 0,
            "prompt_tokens": 0, "completion_tokens": 0}


def _mentions(question: str, value: str) -> bool:
    return len(value) >= 3 and bool(re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", question, re.IGNORECASE))


def _cells(body: str, row_number: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for column, cell_row, value in CELL.findall(body or ""):
        if int(cell_row) != row_number or column in values:
            return {}  # 坐标不一致/重复列时不拼出可能错误的记录。
        values[column.upper()] = value.strip()
    return values


def parse_rows(content: str) -> list[dict]:
    """把 xlsx 片段解析成 [{sheet,row,values,source_index?}]；跨行单元格拼回同一行。"""
    rows: list[dict] = []
    current: dict | None = None
    for raw in (content or "").replace("\x00", "").splitlines():
        line = raw.strip()
        match = ROW_LINE.match(line)
        if match:
            if current is not None:
                current["values"] = _cells(current["body"], current["row"])
                rows.append(current)
            current = {"sheet": match.group(1), "row": int(match.group(2)),
                       "body": match.group(3)}
        elif current is not None:
            current["body"] += "\n" + line
    if current is not None:
        current["values"] = _cells(current["body"], current["row"])
        rows.append(current)
    return rows


def _find_column(table: dict[str, str], keyword: str) -> str | None:
    matches = [column for column, label in table.items()
               if re.sub(r"\s+", " ", label.strip()).casefold() in COLUMN_LABELS[keyword]]
    return matches[0] if len(matches) == 1 else None


def _headers(rows: list[dict]) -> dict[tuple, dict]:
    """只采纳同文档/工作表前五行的明确字段表头；冲突时不裁定。"""
    headers: dict[tuple, dict] = {}
    ambiguous: set[tuple] = set()
    for row in rows:
        table = row["values"]
        counts = {role: sum(re.sub(r"\s+", " ", value.strip()).casefold() in labels for value in table.values())
                  for role, labels in COLUMN_LABELS.items()}
        if row["row"] > 5 or not counts["name"]:
            continue
        if sum(count > 0 for count in counts.values()) < 2:
            continue
        key = row["table_key"]
        if any(count > 1 for role, count in counts.items() if role not in {"current", "default"}):
            ambiguous.add(key)
            continue
        if key in headers and headers[key]["columns"] != table:
            ambiguous.add(key)
        else:
            headers.setdefault(key, {"columns": table, "source_index": row["source_index"]})
    return {key: table for key, table in headers.items() if key not in ambiguous}


def _requested_attributes(question: str) -> list[str]:
    boundary = r"(?<![A-Za-z0-9_.-]){}(?![A-Za-z0-9_.-])"
    question = re.sub(boundary.format(r"(?:current|actual)\s*value"), "现场当前值", question, flags=re.IGNORECASE)
    question = re.sub(boundary.format(r"default\s*value"), "默认值", question, flags=re.IGNORECASE)
    question = re.sub(boundary.format(r"example\s*value"), "", question, flags=re.IGNORECASE)
    found = []
    for attribute, words in ATTRIBUTE_WORDS.items():
        matched = (any(re.search(boundary.format(re.escape(word)), question, re.IGNORECASE) for word in words)
                   if attribute in {"value", "default"} else any(word.lower() in question.lower() for word in words))
        if matched:
            found.append(attribute)
    return found


def _field_rows(question: str, rows: list[dict], headers: dict[tuple, dict]) -> list[dict]:
    """按 Name 列字面匹配问题中的字段名；否则按 P<数字> 匹配 Index 列值。"""
    wanted_indices = set(PX.findall(question))
    if len(wanted_indices) > 1:
        return []  # 多字段比较仍交给原路径，不只挑第一个 P 号。
    candidates: list[dict] = []
    for row in rows:
        header = headers.get(row["table_key"])
        if not header or row["values"] == header["columns"]:
            continue
        name_column = _find_column(header["columns"], "name")
        name = (row["values"].get(name_column) or "").strip()
        if _mentions(question, name):
            candidates.append(row)
    if candidates:
        matched = candidates
        names = {row["values"][_find_column(headers[row["table_key"]]["columns"], "name")].casefold()
                 for row in matched}
        if len(names) > 1:
            return []
        if wanted_indices and any(
                row["values"].get(_find_column(headers[row["table_key"]]["columns"], "index"))
                not in wanted_indices for row in matched):
            return []  # 名称与 P 号不一致时不能选一个为准。
        return matched

    px = PX.search(question)
    if not px:
        return []
    wanted = px.group(1)
    matches = []
    for row in rows:
        header = headers.get(row["table_key"])
        if not header or row["values"] == header["columns"]:
            continue
        index_column = _find_column(header["columns"], "index")
        if (row["values"].get(index_column) or "").strip() == wanted:
            matches.append(row)
    return matches


def _current_value_only(question: str, named_files: set[str]) -> bool:
    """正文默认值兜底只接管单一当前值问句，不能截走复合问题。"""
    intent = question.strip()
    for filename in named_files:
        intent = re.sub(
            rf'^(?:仅依据|仅按|根据|依据)\s*《?{re.escape(filename)}》?\s*[,，:：]?\s*',
            '', intent, flags=re.IGNORECASE)
    # ponytail: 只接受明确的单项问法；不尝试用关键词猜出任意复合问题的全部意图。
    return re.fullmatch(
        r'(?:请问\s*)?P\s*\d{1,3}\s*(?:的\s*)?'
        r'(?:现场当前值|当前值|现场值|实测值|实际值|读取值|(?:current|actual)\s*value)'
        r'\s*(?:是?(?:多少|什么))?\s*[?？。]?\s*', intent, re.IGNORECASE) is not None


def build_table_answer(question: str, sources: list[dict]) -> dict | None:
    """返回 {answer, model, ...} 或 None（交给原生成路径）。"""
    if not question or not sources:
        return None
    attributes = _requested_attributes(question)
    if not attributes:
        return None
    named_files = {str(source.get("filename") or "").casefold() for source in sources
                   if str(source.get("filename") or "").lower().endswith((".xlsx", ".docx"))
                   and re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(str(source['filename']))}"
                                 r"(?![A-Za-z0-9_.-])", question, re.IGNORECASE)}
    if not named_files and re.search(r"\.(?:xlsx|docx)(?![A-Za-z0-9_.-])", question, re.IGNORECASE) and any(
            str(source.get("filename") or "").lower().endswith((".xlsx", ".docx")) for source in sources):
        return _answer_result("请确认问题中的文件名并选择对应文档，不能用其它文件的同名字段代答。",
                              "table_clarification")
    named_sheets = set(re.findall(r"(?:工作表|文档表格)《([^》]+)》", question))
    source_rows: dict[int, list[dict]] = {}
    for index, source in enumerate(sources, start=1):
        filename = str(source.get("filename") or "").lower()
        native_docx = filename.endswith(".docx") and re.search(r"^文档表格《", source.get("content") or "", re.MULTILINE)
        if not (filename.endswith(".xlsx") or native_docx) or (named_files and filename.casefold() not in named_files):
            continue
        rows = parse_rows(source.get("content") or "")
        if named_sheets:
            rows = [row for row in rows if row["sheet"] in named_sheets]
        document_key = (("id", source["document_id"]) if source.get("document_id") is not None
                        else ("filename", filename))
        for row in rows:
            row.update(table_key=(document_key, row["sheet"]), source_index=index,
                       filename=source["filename"])
        if rows:
            source_rows[index] = rows
    all_rows = [row for rows in source_rows.values() for row in rows]
    headers = _headers(all_rows)
    mentioned = []
    labels = set().union(*COLUMN_LABELS.values())
    for row in all_rows:
        header = headers.get(row["table_key"])
        name_column = _find_column(header["columns"], "name") if header else None
        values = [value.strip() for column, _number, value in CELL.findall(row["body"])
                  if name_column is None or column == name_column]
        if any(value.casefold() not in labels and _mentions(question, value) for value in values):
            mentioned.append(row)
    if mentioned:
        evidence = [index for row in mentioned for index in
                    (row["source_index"], headers.get(row["table_key"], {}).get("source_index"))
                    if index is not None]
        cite = "".join(f"[{index}]" for index in dict.fromkeys(evidence))
        if any(row["table_key"] not in headers or not row["values"] for row in mentioned):
            return _answer_result(
                f"当前片段中的表头或单元格坐标无法可靠绑定到所问记录，暂不能确定列含义。{cite}\n"
                "请补充或核对同一文件、同一工作表的表头和目标行，不按默认列序推断。",
                "table_clarification")
        wanted = set(PX.findall(question))
        if len(wanted) == 1 and any(row["values"].get(
                _find_column(headers[row["table_key"]]["columns"], "index")) not in wanted
                for row in mentioned):
            return _answer_result(
                f"问题中的字段名与 P 编号不能对应到同一条已核对记录。{cite}\n"
                "请确认字段名或 P 编号；不能自行选其中一个作为标准。", "table_clarification")
    fields = _field_rows(question, all_rows, headers)
    if not fields:
        wanted = set(PX.findall(question))
        if len(wanted) == 1 and not any(word in question for word in ("单元格", "坐标")):
            number = next(iter(wanted))
            identifier = re.compile(rf"(?<![A-Za-z0-9_.-])P\s*{number}(?![A-Za-z0-9_.-])", re.IGNORECASE)
            scoped = [(index, source) for index, source in enumerate(sources, 1)
                      if not named_files or str(source.get("filename") or "").casefold() in named_files]
            coordinate = False
            literal = False
            defaults = []
            reading = False
            for source_index, source in scoped:
                content = source.get("content") or ""
                if str(source.get("filename") or "").lower().endswith(".xlsx") or re.search(
                        r"^文档表格《", content, re.MULTILINE):
                    coordinate |= any(column == "P" and int(row) == int(number)
                                      for column, row, _value in CELL.findall(content))
                    content = CELL.sub(lambda match: " " + match[3] + " ", content)
                literal |= identifier.search(content) is not None
                if identifier.search(content) and re.search(r"默认|\bdefault\b", content, re.IGNORECASE):
                    defaults.append(source_index)
                reading |= re.search(r"当前值|实际值|实测|读取值|实时|current\s*value|actual\s*value",
                                     content, re.IGNORECASE) is not None
            if "current" in attributes and defaults and not reading and _current_value_only(question, named_files):
                cite = "".join(f"[{index}]" for index in defaults)
                return _answer_result(f"现有片段提供的是该编号的默认/建议设置，不能当作设备实时读取值；"
                                      f"无法确认现场实际设置值。{cite}\n请补充对应设备的参数读取记录。")
            if not literal and (coordinate or named_files):
                cite = "".join(f"[{index}]" for index, _source in scoped)
                reason = (f"片段中的 P{number} 是单元格坐标写法，不能据此确认同名参数的含义或现场值。"
                          if coordinate else f"指定文件的现有片段未提供可将 P{number} 绑定为参数的定义或明确 Index 记录。")
                return _answer_result(f"{reason}{cite}\n请确认所问的是参数还是单元格，并补充对应参数定义/读取记录；"
                                      "不能用其它文件的同号条目代替。", "table_clarification")
        return None
    if "default" in attributes:
        intent = question
        for row in fields:
            name = row["values"].get(_find_column(headers[row["table_key"]]["columns"], "name"), "")
            if name:
                intent = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", "", intent,
                                flags=re.IGNORECASE)
        if "default" not in _requested_attributes(intent):
            attributes.remove("default")
            if not attributes:
                return None
    if "usage" in attributes:
        attributes = list(dict.fromkeys(attributes + ["index", "description", "unit"]))

    lines: list[str] = []
    ambiguous = False
    supported = False
    if any(role in attributes for role in ("current", "default")) and PX.search(question) and not any(_mentions(
            question, row["values"].get(_find_column(headers[row["table_key"]]["columns"], "name"), ""))
            for row in fields):
        number = PX.search(question).group(1)
        cite = "".join(f"[{index}]" for index in dict.fromkeys(
            index for row in fields for index in (row["source_index"], headers[row["table_key"]]["source_index"])))
        lines.append(f"以下仅按数字查找 Index={number} 的记录；P{number} 与该索引的别名关系仍需核对，"
                     f"不能由索引单元格直接证明。{cite}")
    if len({tuple(sorted(row["values"].items())) for row in fields}) > 1:
        cites = "".join(f"[{row['source_index']}]" for row in fields)
        lines.append(f"匹配记录存在差异，以下按来源分别列出，不判定唯一标准。{cites}")
    for row in fields:
        sheet = row["sheet"]
        header = headers[row["table_key"]]
        table = header["columns"]
        name_column = _find_column(table, "name")
        cite = "".join(f"[{index}]" for index in
                       dict.fromkeys([row["source_index"], header["source_index"]]))
        name = row["values"].get(name_column, "")
        table_kind = "文档表格" if row["filename"].lower().endswith(".docx") else "工作表"
        lines.append(f"文件《{row['filename']}》{table_kind}《{sheet}》第 {row['row']} 行（字段 {name}）{cite}")
        row_attributes = list(attributes)
        for role, label in (("current", "当前值/实测值"), ("default", "默认值")):
            columns = [column for column, value in table.items()
                       if re.sub(r"\s+", " ", value.strip()).casefold() in COLUMN_LABELS[role]]
            if role not in attributes or len(columns) < 2:
                continue
            ambiguous = True
            row_attributes.remove(role)
            lines.append(f"- 匹配记录含多个{label}列，不能自动选定其中一列；以下仅列原文，请核对所问列。{cite}")
            for column in columns:
                value = row["values"].get(column)
                lines.append(f"- {column}列 {table[column]} 原文：{value}{cite}" if value else
                             f"- {column}列 {table[column]}：当前检索片段未提供对应单元格值。{cite}")
            lines.append(f"- 上述均为文档中的多列记录，不代表已确认的唯一值或设备实时读取值。{cite}")
        for role in row_attributes:
            if role != "usage":
                supported |= bool(row["values"].get(_find_column(table, role)))
        index_column = _find_column(table, "index")
        if "index" in row_attributes:
            value = row["values"].get(index_column)
            if value:
                lines.append(f"- 索引（{index_column}列 {table[index_column]}）："
                             f"{value}；行号 {row['row']} 不是索引值。{cite}")
            else:
                lines.append(f"- 索引：现有资料未提供可靠的 Index 列或对应单元格值；不以行号代替。{cite}")
        if "value" in row_attributes:
            column = _find_column(table, "value")
            value = row["values"].get(column)
            if value:
                lines.append(f"- Value 列原文（{column}列 {table[column]}）：{value}{cite}")
                lines.append(f"- 上述为文档记录，不等于设备此刻的实时读取值；需实时读取确认。{cite}")
            else:
                lines.append(f"- Value：现有资料未提供可靠的 Value 列或对应单元格值，不以其它列代替。{cite}")
        if "default" in row_attributes:
            column = _find_column(table, "default")
            value = row["values"].get(column)
            if value:
                lines.append(f"- 默认值原文（{column}列 {table[column]}）：{value}{cite}")
                lines.append(f"- 上述为文档默认值记录，不等于设备此刻的实时读取值；需实时读取确认。{cite}")
            else:
                lines.append(f"- 默认值：现有资料未提供可靠的 Default 列或对应单元格值，"
                             f"不以 Value、当前值或示例列代替。{cite}")
        if "description" in row_attributes:
            column = _find_column(table, "description")
            value = row["values"].get(column)
            if value:
                lines.append(f"- 字段说明（{column}列 {table[column]}）：{value}{cite}")
            else:
                lines.append(f"- 字段说明：现有资料未提供可靠的 Description 列或对应单元格值。{cite}")
        if "unit" in row_attributes:
            for role in ("unit", "mark"):
                column = _find_column(table, role)
                value = row["values"].get(column)
                label = "Unit" if role == "unit" else "Mark"
                if value:
                    lines.append(f"- 单位原文：{label} 列（{column}列）={value}{cite}")
                else:
                    lines.append(f"- {label} 列：现有资料未提供可靠表头或对应单元格值。{cite}")
        if "current" in row_attributes:
            column = _find_column(table, "current")
            value = row["values"].get(column)
            if value:
                lines.append(f"- 资料记录的当前值（{column}列 {table[column]}）：{value}{cite}")
                unit_column = _find_column(table, "unit")
                unit = row["values"].get(unit_column)
                if unit:
                    lines.append(f"- 当前值单位原文（{unit_column}列 {table[unit_column]}）：{unit}{cite}")
                lines.append(f"- 上述为文档记录，不等于设备此刻的实时读取值；需实时读取确认。{cite}")
            else:
                lines.append("- 现场当前值：现有字段未提供明确当前值列或对应单元格值，不能用索引、默认值或示例代替；"
                             f"无法确认现场实际设置值。{cite}")
        if "usage" in row_attributes:
            lines.append(f"- 以上是原文定义/说明；索引不等于现场设置值，不能仅凭索引补写操作步骤。{cite}")
    return _answer_result("\n".join(lines), "table_clarification" if ambiguous and not supported else "table_answers")
