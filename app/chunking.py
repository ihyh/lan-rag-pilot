"""切块：合并相邻段落到约 max_tokens，超长文本再按 token 边界二次切分并重叠。

真实后端使用模型自带 tokenizer 精确计数与偏移切分；mock 后端（仅测试）退回
字符近似切分，保证离线也能跑通入库/检索流程。
"""
from __future__ import annotations

from dataclasses import dataclass

from .parsing import Unit

_SENTENCE_END = "。！？；.!?;\n"


@dataclass
class Piece:
    text: str
    token_count: int
    page: int | None = None
    paragraph: int | None = None


class TokenizerAdapter:
    """统一 token 计数与长文本切分接口。"""

    def __init__(self, tokenizer=None) -> None:
        self._tokenizer = tokenizer

    @staticmethod
    def _approx_count(text: str) -> int:
        """中文约 0.5 token/字、其余约 0.25 token/字符 的近似（仅测试路径）。"""
        if not text:
            return 0
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        other = len(text) - cjk
        return max(1, (cjk + 1) // 2 + (other + 3) // 4)

    def count(self, text: str) -> int:
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text, add_special_tokens=False))
        return self._approx_count(text)

    def split_long(self, text: str, max_tokens: int, overlap_tokens: int) -> list[tuple[str, int]]:
        """把超长文本切成 ≤max_tokens 的片段，返回 (text, token_count)。"""
        text = text.strip()
        n = self.count(text)
        if n <= max_tokens:
            return [(text, n)]
        if self._tokenizer is None:
            return self._approx_split(text, max_tokens, overlap_tokens)

        enc = self._tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True, truncation=False
        )
        offsets = enc["offset_mapping"]
        total = len(enc["input_ids"])
        pieces: list[tuple[str, int]] = []
        start = 0
        while start < total:
            end = min(start + max_tokens, total)
            snap = None
            for i in range(end - 1, start, -1):
                seg = text[offsets[i][0]:offsets[i][1]]
                if seg and seg[-1] in _SENTENCE_END:
                    snap = i
                    break
            seg_end = (snap + 1) if snap is not None else end
            piece = text[offsets[start][0]:offsets[seg_end - 1][1]].strip()
            if piece:
                pieces.append((piece, self.count(piece)))
            span = seg_end - start
            next_start = seg_end - min(overlap_tokens, span - 1)
            if next_start <= start:
                next_start = start + 1
            start = next_start
        return pieces or [(text, n)]

    def _approx_split(self, text: str, max_tokens: int, overlap_tokens: int) -> list[tuple[str, int]]:
        window = max(16, max_tokens * 2)
        overlap_chars = max(1, overlap_tokens * 2)
        step = max(1, window - overlap_chars)
        pieces: list[tuple[str, int]] = []
        i = 0
        n_text = len(text)
        while i < n_text:
            j = min(n_text, i + window)
            boundary = None
            for k in range(j, i, -1):
                if text[k - 1] in _SENTENCE_END:
                    boundary = k
                    break
            j = boundary if boundary is not None else j
            piece = text[i:j].strip()
            if piece:
                pieces.append((piece, self._approx_count(piece)))
            if j >= n_text:
                break
            i = max(i + 1, j - overlap_chars)
        return pieces or [(text, self._approx_count(text))]


def chunk_units(
    units: list[Unit],
    ta: TokenizerAdapter,
    max_tokens: int,
    overlap_tokens: int,
) -> list[Piece]:
    """把 (页码|段落, 文本) 单元流切成 Piece 列表。

    PDF：页码相同的单元可合并，切块不跨页（保证页码引用准确）。
    DOCX/TXT/MD：全部段落合并为一条流（引用记起始段落号）。
    """
    pieces_out: list[Piece] = []
    buffer_texts: list[str] = []
    buffer_page: int | None = None
    buffer_para: int | None = None
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer_texts, buffer_page, buffer_para, buffer_tokens
        merged = "\n".join(t for t in buffer_texts if t.strip()).strip()
        if merged:
            for text, cnt in ta.split_long(merged, max_tokens, overlap_tokens):
                pieces_out.append(
                    Piece(text=text, token_count=cnt, page=buffer_page, paragraph=buffer_para)
                )
        buffer_texts = []
        buffer_page = None
        buffer_para = None
        buffer_tokens = 0

    for unit in units:
        t = (unit.text or "").strip()
        if not t:
            continue
        tk = ta.count(t)
        if buffer_texts and (buffer_page != unit.page or buffer_tokens + tk > max_tokens):
            flush()
        if not buffer_texts:
            buffer_page = unit.page
            buffer_para = unit.paragraph
        buffer_texts.append(t)
        buffer_tokens += tk
    flush()
    return pieces_out
