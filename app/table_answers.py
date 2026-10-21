"""表格参数题的确定性回答（实验，默认关闭）。

真实问题诊断里，模型看到正确的 xlsx 行仍会把“行号 18”当成“索引 20”，或把 Unit/Mark 列
的单位说反。这类题的关系可由原文结构确定：行号、列字母、表头名称、单元格值都在检索片段里。
本模块只做**可逐字核对**的表格查找：

- 从 xlsx 检索片段解析 `工作表《Sheet》第 N 行：A18=20；B18=...；` 行与表头行；
- 问题点名了字段名（Name 列匹配）或 `P<数字>`（对应 Index 列值）时，定位目标行；
- 按问题问到的属性（索引/字段说明/单位/现场当前值）输出列名=值的直接回答，
  明确行号 ≠ 索引，并区分 Unit 与 Mark 列；
- 资料只有定义、没有实测值时，直接说明“无法确认现场当前值”，不用定义冒充现场值；
- 找不到可靠字段/表头时返回 None，交回原生成路径。

不调用模型、不改检索/切块/向量；输出仍由服务端可核对的原文单元格拼成。
"""
from __future__ import annotations

import re

ROW_LINE = re.compile(r"^工作表《([^》]+)》第\s*(\d+)\s*行：(.*)$")
CELL = re.compile(r"([A-Z]+)(\d+)=([^；;]*)")
PX = re.compile(r"(?<![A-Za-z0-9])P\s*(\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE)
DEFAULT_LABELS = {"A": "Index", "B": "Name", "C": "Unit", "D": "Description", "E": "Mark"}
ATTRIBUTE_WORDS = {
    "index": ("索引", "index"),
    "description": ("字段说明", "描述", "含义", "description"),
    "unit": ("单位", "unit"),
    "current": ("现场当前值", "当前值", "现场值", "实测值", "实际值", "读取值"),
}


def _cells(body: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for column, _row, value in CELL.findall(body or ""):
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
                current["values"] = _cells(current["body"])
                rows.append(current)
            current = {"sheet": match.group(1), "row": int(match.group(2)),
                       "body": match.group(3)}
        elif current is not None:
            current["body"] += "\n" + line
    if current is not None:
        current["values"] = _cells(current["body"])
        rows.append(current)
    return rows


def _headers(rows: list[dict]) -> dict[str, dict[str, str]]:
    """每个工作表的列字母 -> 表头文字；没有表头行时用默认列含义兜底。"""
    headers: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["row"] == 1:
            headers.setdefault(row["sheet"], {}).update(
                {column: value for column, value in row["values"].items() if value}
            )
    return headers


def _label(headers: dict[str, dict[str, str]], sheet: str, column: str) -> str:
    table = headers.get(sheet, {})
    return table.get(column) or DEFAULT_LABELS.get(column, column)


def _find_column(headers: dict[str, dict[str, str]], sheet: str, keyword: str) -> str | None:
    table = headers.get(sheet, {})
    for column, label in table.items():
        if keyword.lower() in (label or "").lower():
            return column
    for column, fallback in DEFAULT_LABELS.items():
        if keyword.lower() in fallback.lower() and (not table or column in table):
            return column
    return None


def _requested_attributes(question: str) -> list[str]:
    found = []
    for attribute, words in ATTRIBUTE_WORDS.items():
        if any(word.lower() in question.lower() for word in words):
            found.append(attribute)
    return found


def _field_rows(question: str, rows: list[dict], headers: dict[str, dict[str, str]]) -> list[dict]:
    """按 Name 列字面匹配问题中的字段名；否则按 P<数字> 匹配 Index 列值。"""
    candidates: list[tuple[int, dict]] = []
    for row in rows:
        if row["row"] == 1:
            continue
        sheet = row["sheet"]
        name_column = _find_column(headers, sheet, "name") or "B"
        index_column = _find_column(headers, sheet, "index") or "A"
        name = (row["values"].get(name_column) or "").strip()
        if len(name) >= 3 and name.lower() in question.lower():
            candidates.append((len(name), row))
    if candidates:
        longest = max(length for length, _row in candidates)
        return [row for length, row in candidates if length == longest]

    px = PX.search(question)
    if not px:
        return []
    wanted = px.group(1)
    matches = []
    for row in rows:
        if row["row"] == 1:
            continue
        sheet = row["sheet"]
        index_column = _find_column(headers, sheet, "index") or "A"
        if (row["values"].get(index_column) or "").strip() == wanted:
            matches.append(row)
    return matches


def build_table_answer(question: str, sources: list[dict]) -> dict | None:
    """返回 {answer, model, ...} 或 None（交给原生成路径）。"""
    if not question or not sources:
        return None
    attributes = _requested_attributes(question)
    if not attributes:
        return None
    source_rows: dict[int, list[dict]] = {}
    for index, source in enumerate(sources, start=1):
        filename = str(source.get("filename") or "").lower()
        if not filename.endswith(".xlsx"):
            continue
        rows = parse_rows(source.get("content") or "")
        if rows:
            source_rows[index] = rows
    if not source_rows:
        return None
    all_rows = [row for rows in source_rows.values() for row in rows]
    headers = _headers(all_rows)
    fields = _field_rows(question, all_rows, headers)
    if not fields:
        return None

    def citation_for(row: dict) -> int | None:
        for source_index, rows in source_rows.items():
            if any(candidate is row for candidate in rows):
                return source_index
        return None

    lines: list[str] = []
    for row in fields:
        sheet = row["sheet"]
        index_column = _find_column(headers, sheet, "index") or "A"
        unit_column = _find_column(headers, sheet, "unit") or "C"
        mark_column = _find_column(headers, sheet, "mark") or "E"
        description_column = _find_column(headers, sheet, "description") or "D"
        name_column = _find_column(headers, sheet, "name") or "B"
        source_index = citation_for(row)
        cite = f"[{source_index}]" if source_index else ""
        name = row["values"].get(name_column, "")
        lines.append(f"工作表《{sheet}》第 {row['row']} 行（字段 {name}）{cite}")
        if "index" in attributes:
            lines.append(f"- 索引（{index_column}列 {_label(headers, sheet, index_column)}）："
                         f"{row['values'].get(index_column, '')}；行号 {row['row']} 不是索引值。")
        if "description" in attributes:
            lines.append(f"- 字段说明（{description_column}列 "
                         f"{_label(headers, sheet, description_column)}）："
                         f"{row['values'].get(description_column, '')}")
        if "unit" in attributes:
            lines.append(f"- 单位：Unit 列（{unit_column}列）="
                         f"{row['values'].get(unit_column, '')}；"
                         f"Mark 列（{mark_column}列）="
                         f"{row['values'].get(mark_column, '')}。")
        if "current" in attributes:
            lines.append("- 现场当前值：检索片段只有该字段的定义，没有该设备的实测/读取值记录，"
                         "无法确认现场实际设置值。")
    return {
        "answer": "\n".join(lines),
        "model": "table_answers",
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
