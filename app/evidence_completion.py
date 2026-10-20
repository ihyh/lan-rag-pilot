"""按问题点名的字面标识符补齐原文来源（跨文档保留不同定义，默认关闭）。

真实问题诊断显示：用户问题明确点名了代码/字段（如 2,1、3,23、bit4、Input Monitor Time），
但向量检索只选中了其中一个文档的片段，另一份资料里同号/同名字段的原文没有进上下文，
模型就只能看到一个定义，容易自行裁定或漏答。

本模块只做可逐字核对的补证：
- 从问题中提取明确标识符（告警代码对、十六进制码、SxFy、字母+数字字段、多词英文短语、
  引号内文句），不提取停用词；
- 在会话范围内按字面搜索 ready 文档的 chunks，把包含该标识符的原文片段作为**独立来源**
  附加（保留真实 document/chunk/page，score 留空，matched_literal 记录命中的字面）；
- 每个标识符优先覆盖不同文档（保留不同定义），同一文档只取一个片段；总数有上限；
- xlsx 片段若不在文档首段，额外附上该文档首段以保留表头/字段语义。

不重切块、不重算向量、不改召回排序与引用编号；默认关闭，需显式开启并做前后对照。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MAX_EXTRA_SOURCES = 4
MAX_PER_LITERAL = 3
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


def _search_rows(db: sqlite3.Connection, token: str, document_ids: set[int] | None,
                 limit: int = 40) -> list[sqlite3.Row]:
    clauses = ["d.status = 'ready'", "lower(c.content) LIKE ?"]
    params: list[object] = [f"%{token.lower()}%"]
    if document_ids:
        clauses.append("c.document_id IN (%s)" % ",".join("?" * len(document_ids)))
        params.extend(sorted(document_ids))
    sql = (
        "SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph,"
        " c.content, d.filename "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE " + " AND ".join(clauses) + " ORDER BY c.id LIMIT ?"
    )
    params.append(limit)
    return list(db.execute(sql, params).fetchall())


def _header_row(db: sqlite3.Connection, document_id: int) -> sqlite3.Row | None:
    return db.execute(
        "SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph,"
        " c.content, d.filename "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.document_id = ? AND c.seq = 0",
        (document_id,),
    ).fetchone()


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
        rows = _search_rows(db, token, document_ids)
        if not rows:
            continue
        taken_documents: set[int] = set()
        added_for_token = 0
        for row in rows:
            if added >= max_extra or added_for_token >= MAX_PER_LITERAL:
                break
            document_id = int(row["document_id"])
            if document_id in taken_documents:
                continue
            if add(row, "question_literal", token):
                taken_documents.add(document_id)
                added_for_token += 1
        # 同一文档内可能有多个匹配片段（例如表格目标行与相邻行），单个文档时允许补足。
        if not taken_documents:
            continue
        for row in rows:
            if added >= max_extra or added_for_token >= MAX_PER_LITERAL:
                break
            if int(row["document_id"]) not in taken_documents:
                continue
            if add(row, "question_literal", token):
                added_for_token += 1

    # xlsx 非首段没有表头语义：附上该文档首段（每文档最多一次）。
    xlsx_documents = {
        int(source["document_id"]) for source in result
        if str(source.get("filename") or "").lower().endswith(".xlsx")
    }
    for document_id in sorted(xlsx_documents):
        if added >= max_extra:
            break
        header = _header_row(db, document_id)
        if header is None or int(header["chunk_id"]) in used:
            continue
        add(header, "table_header", None)
    return result
