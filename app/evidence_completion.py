"""按问题点名的字面标识符补齐原文来源（跨文档保留不同定义，默认关闭）。

真实问题诊断显示：用户问题明确点名了代码/字段（如 2,1、3,23、bit4、Input Monitor Time），
但向量检索只选中了其中一个文档的片段，另一份资料里同号/同名字段的原文没有进上下文，
模型就只能看到一个定义，容易自行裁定或漏答。

本模块只做可逐字核对的补证：
- 从问题中提取明确标识符（告警代码对、十六进制码、SxFy、字母+数字字段、多词英文短语、
  引号内文句），不提取停用词；
- 在会话范围内按字面搜索 ready 文档的 chunks，先用 SQL 粗召回，再用**带边界的正则**
  做精确复核（2,1 不匹配 12,10；bit4 不匹配 bit40；A_B 不匹配 AXB），LIKE 元字符已转义；
- 按文档分批召回，先保证不同文档覆盖，再补同文档片段，避免早期大文档的重复片段挤掉
  后来文档里的另一份定义；
- 把命中片段作为**独立来源**附加（保留真实 document/chunk/page，score 留空，
  matched_literal 记录命中的字面）；
- xlsx 命中片段按**目标工作表**查找真正表头（不拿整份文件第一个 chunk 冒充表头）；
  找不到可靠表头时不标记 table_header。

不重切块、不重算向量、不改召回排序与引用编号；默认关闭，需显式开启并做前后对照。
"""
from __future__ import annotations

import re
import sqlite3

from .table_answers import parse_rows

MAX_EXTRA_SOURCES = 4
MAX_PER_LITERAL = 3
MAX_DOCUMENTS_PER_LITERAL = 24
MAX_ROWS_PER_DOCUMENT = 2
MAX_SCAN_PER_DOCUMENT = 200
SCAN_BATCH = 32
HEADER_LABELS = {
    "index", "name", "unit", "description", "mark",
    "序号", "名称", "单位", "说明", "描述", "参数", "标记",
}

HEX = re.compile(r"(?<![0-9A-Za-z])0x[0-9A-Fa-f]{2,4}(?![0-9A-Za-z])")
CODE_PAIR = re.compile(r"(?<!\d)(\d{1,3})\s*,\s*(\d{1,3})(?!\d)")
SXF = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{1,2}[A-Z]\d{1,2}(?![A-Za-z0-9])")
IDENT_DIGIT = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]{1,4}\d{1,4}(?![A-Za-z0-9_])")
MULTIWORD = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_-]*"
    r"(?:\s+[A-Za-z][A-Za-z0-9_-]*){1,3}"
    r"(?![A-Za-z0-9_])"
)
QUOTED = re.compile(r"[“\"']([^”\"']{2,30})[”\"']")
SHEET_NAME = re.compile(r"工作表《([^》]+)》第")
STOPWORDS = {
    "plm", "plus", "plus500", "pluspro", "e84", "host", "ascii", "secs", "hex",
    "rfid", "foup", "oht", "auto", "manual", "mapping", "load", "unload",
    "byte", "bit", "mode", "revision", "company", "secret", "confidential",
    "the", "and", "for", "with", "from", "this", "that",
}


def question_literals(question: str, limit: int = 6) -> list[str]:
    """提取问题中可逐字定位的标识符；保留原大小写与写法。"""
    question = question or ""
    pairs: list[str] = [f"{first},{second}" for first, second in CODE_PAIR.findall(question)]
    found: list[str] = []
    found.extend(HEX.findall(question))
    found.extend(SXF.findall(question))
    for match in IDENT_DIGIT.findall(question):
        if match.lower() not in STOPWORDS:
            found.append(match)
    for match in MULTIWORD.findall(question):
        words = match.split()
        if len(words) < 2:
            continue
        if all(word.lower() in STOPWORDS for word in words):
            continue
        if len(match) < 6:
            continue
        found.append(match)
    for match in QUOTED.findall(question):
        found.append(match)
    result: list[str] = []
    for token in pairs + found:
        token = token.strip()
        if len(token) < 2 or token in result:
            continue
        # 告警代码对（2,1 / 3,23）没有字母也必须保留；其余纯数字/标点不单独触发补证。
        if not any(char.isalpha() for char in token) and token not in pairs:
            continue
        result.append(token)
        if len(result) >= limit:
            break
    return result


def _literal_matcher(token: str) -> re.Pattern[str]:
    """按标识符类型构造带边界的匹配；SQL 粗召回后必须再过这一层。"""
    if CODE_PAIR.fullmatch(token.replace(" ", "")):
        first, second = token.replace(" ", "").split(",")
        return re.compile(rf"(?<!\d){first}\s*,\s*{second}(?!\d)")
    if HEX.fullmatch(token):
        return re.compile(rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])", re.IGNORECASE)
    if SXF.fullmatch(token):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", re.IGNORECASE)
    if IDENT_DIGIT.fullmatch(token):
        return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", re.IGNORECASE)
    if " " in token.strip():
        words = [re.escape(word) for word in token.split()]
        joined = r"\s+".join(words)
        return re.compile(
            rf"(?<![A-Za-z0-9_]){joined}(?![A-Za-z0-9_])", re.IGNORECASE
        )
    # 引号内短句/其它：按字面查找；纯符号串两端不强制词边界。
    return re.compile(re.escape(token), re.IGNORECASE)


def _like_escape(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _scope_clause(document_ids: set[int] | None) -> tuple[str, list[object]]:
    if not document_ids:
        return "", []
    return " AND c.document_id IN (%s)" % ",".join("?" * len(document_ids)), sorted(document_ids)


def _candidate_rows(
    db: sqlite3.Connection,
    token: str,
    document_ids: set[int] | None,
    matcher: re.Pattern[str],
) -> list[sqlite3.Row]:
    """按文档分批召回，并按精确匹配后的有效候选计名额。

    不能在精确匹配前固定截断：前面的伪匹配（如 bit40 之于 bit4）会占满窗口，
    把后面的真匹配挤掉；这里按 id 续读，直到每份文档找到足够精确匹配或达到扫描上限。
    """
    like = "%" + _like_escape(token) + "%"
    scope_sql, scope_params = _scope_clause(document_ids)
    doc_rows = list(db.execute(
        "SELECT DISTINCT c.document_id FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE d.status = 'ready' AND lower(c.content) LIKE lower(?) ESCAPE '\\'" + scope_sql +
        " ORDER BY c.document_id LIMIT ?",
        [like, *scope_params, MAX_DOCUMENTS_PER_LITERAL],
    ))
    rows: list[sqlite3.Row] = []
    for doc_row in doc_rows:
        matched: list[sqlite3.Row] = []
        last_id = 0
        scanned = 0
        while len(matched) < MAX_ROWS_PER_DOCUMENT and scanned < MAX_SCAN_PER_DOCUMENT:
            batch = list(db.execute(
                "SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph,"
                " c.content, d.filename "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE c.document_id = ? AND c.id > ? "
                "AND lower(c.content) LIKE lower(?) ESCAPE '\\' "
                "ORDER BY c.id LIMIT ?",
                (int(doc_row["document_id"]), last_id, like, SCAN_BATCH),
            ))
            if not batch:
                break
            scanned += len(batch)
            last_id = int(batch[-1]["chunk_id"])
            for row in batch:
                if matcher.search(row["content"] or ""):
                    matched.append(row)
                    if len(matched) >= MAX_ROWS_PER_DOCUMENT:
                        break
        rows.extend(matched)
    return rows


def _as_source(row: sqlite3.Row, relation: str, matched: str | None = None) -> dict:
    source = {
        "chunk_id": int(row["chunk_id"]),
        "document_id": int(row["document_id"]),
        "seq": int(row["seq"]),
        "filename": row["filename"],
        "page": row["page"],
        "paragraph": row["paragraph"],
        "score": None,
        "content": row["content"],
        "relation": relation,
    }
    if matched is not None:
        source["matched_literal"] = matched
    return source


def _sheet_names(content: str) -> list[str]:
    names: list[str] = []
    for name in SHEET_NAME.findall(content or ""):
        if name not in names:
            names.append(name)
    return names


def _valid_header(values: dict[str, str]) -> bool:
    labels = [value.strip().lower() for value in values.values() if value.strip()]
    recognized = sum(1 for label in labels if label in HEADER_LABELS)
    has_name = any(label in {"name", "名称"} for label in labels)
    return recognized >= 2 and (has_name or recognized >= 3)


def _header_chunks(db: sqlite3.Connection, document_id: int, content: str) -> list[sqlite3.Row]:
    """按目标行所属工作表查找真正表头；找不到列名行时不回退到标题/文件首段。"""
    headers: list[sqlite3.Row] = []
    seen: set[int] = set()
    for sheet in _sheet_names(content):
        pattern = "%工作表《" + _like_escape(sheet) + "》第%"
        best: tuple[int, sqlite3.Row] | None = None
        for chunk in db.execute(
            "SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph,"
            " c.content, d.filename "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE c.document_id = ? AND lower(c.content) LIKE lower(?) ESCAPE '\\' "
            "ORDER BY c.id LIMIT 50",
            (document_id, pattern),
        ):
            for parsed in parse_rows(chunk["content"] or ""):
                if parsed["sheet"] != sheet or parsed["row"] > 5:
                    continue
                if not _valid_header(parsed["values"]):
                    continue
                if best is None or parsed["row"] < best[0]:
                    best = (parsed["row"], chunk)
        if best is not None:
            chunk = best[1]
            chunk_id = int(chunk["chunk_id"])
            if chunk_id not in seen:
                seen.add(chunk_id)
                headers.append(chunk)
    return headers


def attach_literal_evidence(
    db: sqlite3.Connection,
    sources: list[dict],
    question: str,
    *,
    document_ids: set[int] | None = None,
    max_extra: int = MAX_EXTRA_SOURCES,
) -> list[dict]:
    """在选段之后补入问题点名字面标识符对应的原文片段；返回新列表。"""
    tokens = question_literals(question)
    if not tokens or max_extra <= 0:
        return list(sources)
    used = {int(source["chunk_id"]) for source in sources if source.get("chunk_id") is not None}
    result = list(sources)
    added = 0

    def add(row: sqlite3.Row, relation: str, matched: str | None) -> bool:
        nonlocal added
        chunk_id = int(row["chunk_id"])
        if chunk_id in used or added >= max_extra:
            return False
        used.add(chunk_id)
        result.append(_as_source(row, relation, matched))
        added += 1
        return True

    for token in tokens:
        if added >= max_extra:
            break
        matcher = _literal_matcher(token)
        rows = _candidate_rows(db, token, document_ids, matcher)
        if not rows:
            continue
        taken_documents: set[int] = set()
        added_for_token = 0
        # 第一轮：每个文档只取一段，保证跨文档定义都能进入。
        for row in rows:
            if added >= max_extra or added_for_token >= MAX_PER_LITERAL:
                break
            document_id = int(row["document_id"])
            if document_id in taken_documents:
                continue
            if add(row, "question_literal", token):
                taken_documents.add(document_id)
                added_for_token += 1
        # 第二轮：同一文档的其它命中片段（如表格目标行）在容量内补足。
        for row in rows:
            if added >= max_extra or added_for_token >= MAX_PER_LITERAL:
                break
            if int(row["document_id"]) not in taken_documents:
                continue
            if add(row, "question_literal", token):
                added_for_token += 1

    # xlsx 命中片段按目标工作表补真正表头；找不到可靠表头时不标记。
    header_checked: set[tuple[int, str]] = set()
    for source in list(result):
        if added >= max_extra:
            break
        if not str(source.get("filename") or "").lower().endswith(".xlsx"):
            continue
        document_id = int(source["document_id"])
        for sheet in _sheet_names(source.get("content") or ""):
            if (document_id, sheet) in header_checked:
                continue
            header_checked.add((document_id, sheet))
            if added >= max_extra:
                break
            for header in _header_chunks(db, document_id, source.get("content") or ""):
                if int(header["chunk_id"]) in used:
                    continue
                add(header, "table_header", None)
                break
    return result
