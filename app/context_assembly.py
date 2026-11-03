"""按原文结构补齐被页/块边界拆开的证据续段（小范围、可关闭）。

背景：参数表、步骤、定义在 PDF 页界或切块边界处被拆开时，检索只返回其中一段，
模型看到的是孤立的一行。这里只在**能够从原文结构判断**时补下一段：
- 当前片段末尾是未完结的句子/列表（没有句末标点，且忽略水印噪声行、括注行）；
- 或下一段开头明显是续写（Byte 2、bit4、续表、第 N 步、注：等明确标记）。

不扩大 chunk、不重切块、不重算向量，也不让模型补造关系；只把同文档下一段作为
独立来源附在锚点之后，来源编号/页码仍各自真实。默认关闭：
`RAG_CONTEXT_CONTINUATION=0`，需显式开启并做前后对照。

复核后收紧的边界：
- 不处理 DOCX：现有解析先输出全部普通段落、再输出全部表格，`seq` 邻接不等于原文邻接；
- 不凭“数字+点/顿号”推断续写或章节：底层章节标题会整体跳过；新章节出现在续文之后时，
  只保留新章节之前的原文，不把无关新章一起带入；
- 无法确认时宁可不补，不制造错误关联。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MAX_EXTRA_SOURCES = 2
# 只有页/段式文档值得补续段；xlsx 行语义靠表头，DOCX 解析顺序不可靠，均不在此处扩。
CONTINUATION_SUFFIXES = {".pdf", ".txt", ".md"}

# 明确的续写标记；数字+点/顿号（6. / 2、）不作为续写依据。
CONTINUATION_START = re.compile(
    r"^\s*(?:"
    r"[（(]?续|续表|续页|续上|"
    r"以及|同时|此外|另外|但|若|如果|如遇|"
    r"第\s*[0-9一二三四五六七八九十]|"
    r"注\s*[：:]|步骤|"
    r"Byte\s*\d|bit\s*\d|P[0-9]{1,3}\b"
    r")",
    re.IGNORECASE,
)
# 新章节/新标题：顶层与多级数字标题、中文章节、Markdown 标题。
# 注意：仅“数字+点/小数值”不算章节；由 _looks_like_new_section 结合后续文字/单位判断。
NEW_SECTION_HEADING = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9一二三四五六七八九十]+\s*[章节部分]|"
    r"[一二三四五六七八九十]+、|"
    r"#{1,6}\s+\S"
    r")"
)
NUMBER_HEADING = re.compile(r"^\s*([0-9]{1,3}(?:\.[0-9]{1,3})*)[.、]\s*(.*)$")
DOTTED_HEADING = re.compile(r"^\s*([0-9]{1,3}(?:\.[0-9]{1,3})+)\s+(.*)$")
UNIT_WORDS = {
    "second", "seconds", "sec", "ms", "millisecond", "milliseconds", "mm",
    "min", "minutes", "minute", "%", "秒", "毫秒", "分钟", "毫米", "次", "个",
}


def _looks_like_new_section(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False
    if NEW_SECTION_HEADING.match(text):
        return True
    for pattern in (NUMBER_HEADING, DOTTED_HEADING):
        match = pattern.match(text)
        if not match:
            continue
        rest = (match.group(match.lastindex) or "").strip()
        if not rest or not re.match(r"^[A-Za-z\u4e00-\u9fff]", rest):
            # 数字后仍是数字（如 0.5 / 1.2.5 被首个模式提前切开）时继续尝试下一模式。
            continue
        first_word = re.split(r"[\s.,;:，。；：)]", rest, maxsplit=1)[0].lower()
        if first_word in UNIT_WORDS:
            return False
        return True
    return False
NOISE_LINE = re.compile(r"(?:fortrend|confidential|no\s*copy)", re.IGNORECASE)
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


def _trim_before_new_section(text: str) -> tuple[str, bool]:
    """把“续文后才出现的新章节”截掉；返回 (保留内容, 是否遇到新章节)。"""
    lines = (text or "").replace("\x00", "").replace("\ufeff", "").splitlines()
    for index, line in enumerate(lines):
        if _looks_like_new_section(line):
            return "\n".join(lines[:index]), True
    return text, False


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
        raw_content = following["content"] or ""
        if chunk_id in used or not raw_content.strip():
            continue
        candidate, has_new_section = _trim_before_new_section(raw_content)
        if has_new_section and not candidate.strip():
            # 下一页开头就是新章节，不能当作续文。
            continue
        explicit = _starts_continuation(candidate)
        if not explicit and not _ends_incomplete(source.get("content") or ""):
            continue
        used.add(chunk_id)
        entry = {
            "chunk_id": chunk_id,
            "document_id": int(following["document_id"]),
            "seq": int(following["seq"]),
            "filename": following["filename"],
            "page": following["page"],
            "paragraph": following["paragraph"],
            "score": None,
            "content": candidate,
            "continuation_of": int(source["chunk_id"]),
        }
        if has_new_section:
            entry["trimmed_at_section"] = True
        result.append(entry)
        added += 1
    return result
