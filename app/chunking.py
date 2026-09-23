"""切块：合并相邻段落到约 max_tokens，超长文本再按 token 边界二次切分并重叠。

真实后端使用模型自带 tokenizer 精确计数与偏移切分；mock 后端（仅测试）退回
字符近似切分，保证离线也能跑通入库/检索流程。
"""
from __future__ import annotations

from dataclasses import dataclass

from .parsing import Unit

_SENTENCE_END = "。！？；.!?;\n"


def check_split_params(max_tokens: int, overlap_tokens: int) -> None:
    """切分参数的合法区间：``max_tokens > 0`` 且 ``0 <= overlap < max_tokens``。

    重叠为负会让下一片从上一片末尾**之后**开始，直接丢掉原文；重叠不小于窗口会让下一片
    不前进甚至倒退。两种情况都会破坏"覆盖原文"，所以在入口拦住而不是等检索阶段才发现。
    """
    if max_tokens <= 0:
        raise ValueError(f"max_tokens 必须为正整数：{max_tokens}")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError(
            f"overlap_tokens 必须满足 0 <= overlap < max_tokens："
            f"overlap={overlap_tokens}, max_tokens={max_tokens}"
        )


def min_span(max_tokens: int, overlap_tokens: int) -> int:
    """接受句末断点所需的最小跨度。

    取 ``max(overlap + 1, max_tokens - overlap)``。下界 ``overlap + 1`` 是关键：它保证
    **刚被采用过的断点在下一轮必然被拒绝**（下一片起点到该断点的距离正好是 overlap，
    小于下界），因此"同一断点处连续缩短的后缀级联"在结构上不可能出现。
    """
    return max(overlap_tokens + 1, max_tokens - overlap_tokens)


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
        """把超长文本切成 ≤max_tokens 的片段，返回 (text, token_count)。

        断点选择有一条**最小跨度**约束（见 ``min_span``）：句末符只有在离当前起点足够远时
        才被采纳，否则整窗切分。没有这条约束时会退化成"同一断点处连续缩短的后缀级联"——
        实测某个 xlsx 缓冲（1241 token）产出 64 片、其中 60 片是 token 数 396→2 的递减后缀；
        某个 PDF 页（676 token）产出 15 片、其中 13 片由 14→2 递减。这些碎片既没有证据价值，
        也白占入库与嵌入预算。

        注意：本方法只保证"不产生同断点级联"和"覆盖完整"，**不禁止有意重叠**。当
        ``overlap`` 接近 ``max_tokens`` 时步长会很小、产出接近滑动窗口式的近似重复片段
        （步长下界为 ``max_tokens - 2*overlap``），这是该配置的固有代价，故默认值取 60/400。
        """
        check_split_params(max_tokens, overlap_tokens)
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
        need = min_span(max_tokens, overlap_tokens)
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
            # 末尾窗口一律取整段（保持原有的收尾行为：最后一片可以是短的）；
            # 其余窗口只在断点离 start 足够远时采纳，否则用整窗。
            # 断点太近时不采纳是必需的：否则这一片几乎没有内容，而且下一轮会以同一个断点
            # 为界继续收缩，逐轮退化成大量同尾后缀。
            if end == total:
                seg_end = end
            elif snap is not None and (snap + 1 - start) >= need:
                seg_end = snap + 1
            else:
                seg_end = end
            piece = text[offsets[start][0]:offsets[seg_end - 1][1]].strip()
            if piece:
                pieces.append((piece, self.count(piece)))
            if seg_end == total:
                break  # 最后一块已覆盖末尾，不再逐字生成重叠尾片段。
            span = seg_end - start
            next_start = seg_end - min(overlap_tokens, span - 1)
            if next_start <= start:  # 终止保险；上面的 need 规则使其不可达
                next_start = start + 1
            start = next_start
        return pieces or [(text, n)]

    def _approx_split(self, text: str, max_tokens: int, overlap_tokens: int) -> list[tuple[str, int]]:
        """mock/无 tokenizer 时的字符近似切分，规则与 ``split_long`` 一致。"""
        window = max(16, max_tokens * 2)
        overlap_chars = max(0, overlap_tokens * 2)
        need = max(overlap_chars + 1, window - overlap_chars)
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
            # 同 token 路径：断点太靠近 i 时改用整窗，避免同断点处的后缀级联。
            j = boundary if (boundary is not None and boundary - i >= need) else j
            piece = text[i:j].strip()
            if piece:
                pieces.append((piece, self._approx_count(piece)))
            if j >= n_text:
                break
            i = max(i + 1, j - overlap_chars)  # 保险；上面的 need 规则使其不可达
        return pieces or [(text, self._approx_count(text))]


def chunk_units(
    units: list[Unit],
    ta: TokenizerAdapter,
    max_tokens: int,
    overlap_tokens: int,
) -> list[Piece]:
    """把 (页码|段落, 文本) 单元流切成 Piece 列表。

    PDF：页码相同的单元可合并，切块不跨页（保证页码引用准确）。
    DOC/DOCX/TXT/MD：全部段落合并为一条流（引用记起始段落号）。
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
