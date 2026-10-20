"""按原文结构补齐被页/块边界拆开的证据续段（小范围、可关闭）。

背景：参数表、步骤、定义在 PDF 页界或切块边界处被拆开时，检索只返回其中一段，
模型看到的是孤立的一行。这里只在**能够从原文结构判断**时补下一段：
- 当前片段末尾是未完结的句子/列表（没有句末标点，且忽略水印噪声行）；
- 或下一段开头明显是续写（Byte 2、bit4、续表、第 N 步、编号条目等）。
不扩大 chunk、不重切块、不重算向量，也不让模型补造关系；只把同文档下一段作为
独立来源附在锚点之后，来源编号/页码仍各自真实。

默认关闭：RAG_CONTEXT_CONTINUATION=0，需显式开启并做前后对照。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MAX_EXTRA_SOURCES = 2
# 只有页/段式文档值得补续段；xlsx 的行语义靠表头，不在此处扩。
CONTINUATION_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}

CONTINUATION_START = re.compile(
    r"^\s*(?:"
    r"[（(]?续|续表|续页|续上|"
    r"以及|同时|此外|另外|但|若|如果|如遇|"
    r"第\s*[0-9一二三四五六七八九十]|"
    r"[0-9]+\s*[、.．)）]|"
    r"注\s*[：:]|步骤|"
    r"Byte\s*\d|bit\s*\d|P[0-9]{1,3}\b|[A-Z]{2,}[_0-9]*[：:=]"
    r")",
    re.IGNORECASE,
)
NOISE_LINE = re.compile(r"(?:fortrend|confidential|no\s*copy)", re.IGNORECASE)
NEW_SECTION = re.compile(
    r"^\s*(?:"
    r"[0-9]+(?:\.[0-9]+)+\s+\S|"
    r"第\s*[0-9一二三四五六七八九十]+\s*[章节部分]|"
    r"[一二三四五六七八九十]+、"
    r")"
)
TERMINALS = tuple("。！？.!?:：;；）)]】》")


def _meaningful_lines(text: str) -> list[str]:
    text = (text or "").replace("\x00", "").replace("\ufeff", "")
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if NOISE_LINE.search(line):
            continue
        if len(line) <= 2 and not line.isalnum():
            continue
        lines.append(line)
    return lines


def _ends_incomplete(text: str) -> bool:
    lines = _meaningful_lines(text)
    if not lines:
        return False
    last = lines[-1]
    # 括注行（如“[用于 PLUS和设备的教导器开发]”）不是未完结句子的判据。
    if re.fullmatch(r"[\[\(（【][^\]\)）】]*[\]\)）】]", last):
        return False
    return not last.endswith(TERMINALS)


def _starts_continuation(text: str) -> bool:
    lines = _meaningful_lines(text)
    if not lines:
        return False
    # PDF 续页开头常带页眉/页码，真正的续写标记可能出现在前几行。
    for line in lines[:5]:
        if CONTINUATION_START.match(line):
            return True
    return False


def _looks_like_new_section(text: str) -> bool:
    lines = _meaningful_lines(text)
    for line in lines[:5]:
        if NEW_SECTION.match(line):
            return True
    return False


def _next_chunk(db: sqlite3.Connection, source: dict) -> sqlite3.Row | None:
    try:
        seq = int(source["seq"])
        document_id = int(source["document_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return db.execute(
        "SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph, c.content,"
        " d.filename "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE c.document_id = ? AND c.seq = ?",
        (document_id, seq + 1),
    ).fetchone()


def attach_continuations(
    db: sqlite3.Connection,
    sources: list[dict],
    *,
    max_extra: int = MAX_EXTRA_SOURCES,
) -> list[dict]:
    """在选段之后，按原文结构附加同文档下一段；返回新列表（不修改入参）。"""
    if not sources or max_extra <= 0:
        return list(sources)
    used = {int(source["chunk_id"]) for source in sources if source.get("chunk_id") is not None}
    result: list[dict] = []
    added = 0
    for source in sources:
        result.append(source)
        if added >= max_extra:
            continue
        suffix = Path(str(source.get("filename") or "")).suffix.lower()
        if suffix not in CONTINUATION_SUFFIXES:
            continue
        following = _next_chunk(db, source)
        if following is None:
            continue
        chunk_id = int(following["chunk_id"])
        if chunk_id in used or not (following["content"] or "").strip():
            continue
        if _looks_like_new_section(following["content"] or ""):
            continue
        if not (_ends_incomplete(source.get("content") or "")
                or _starts_continuation(following["content"] or "")):
            continue
        used.add(chunk_id)
        result.append({
            "chunk_id": chunk_id,
            "document_id": int(following["document_id"]),
            "seq": int(following["seq"]),
            "filename": following["filename"],
            "page": following["page"],
            "paragraph": following["paragraph"],
            "score": None,
            "content": following["content"],
            "continuation_of": int(source["chunk_id"]),
        })
        added += 1
    return result
