"""切块：合并相邻段落到约 max_tokens，超长文本再按 token 边界二次切分并重叠。

真实后端使用模型自带 tokenizer 精确计数与偏移切分；mock 后端（仅测试）退回
字符近似切分，保证离线也能跑通入库/检索流程。
"""
from __future__ import annotations

from dataclasses import dataclass

from .parsing import ParseError, Unit

_SENTENCE_END = "。！？；.!?;\n"


def piece_budget_error(label: str, limit: int) -> ParseError:
    """切片数超过预算时统一的错误（含调用方已有的 code，便于上层原样映射）。"""
    return ParseError(
        f"{label}产生的切片已超过上限 {limit}，已在生成过程中停止。"
        "请拆分文件后分批上传，或调大 RAG_PARSE_MAX_CHUNKS"
        "（调大前请先确认服务器内存足够）；"
        "若重叠接近切块窗口，请调小 RAG_CHUNK_OVERLAP_TOKENS——"
        "此时有效步长会塌到 1，切片数会随文本长度剧增。",
        code="too_many_chunks",
    )


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

    @staticmethod
    def _budget_exceeded(pieces: list, max_pieces: int | None) -> bool:
        """切片预算的**单一定义**：两条切分路径都用它，避免两处判断漂移。"""
        return max_pieces is not None and len(pieces) >= max_pieces

    def split_long(self, text: str, max_tokens: int, overlap_tokens: int,
                   max_pieces: int | None = None) -> list[tuple[str, int]]:
        """把超长文本切成 ≤max_tokens 的片段，返回 (text, token_count)。

        断点选择有一条**最小跨度**约束（见 ``min_span``）：句末符只有在离当前起点足够远时
        才被采纳，否则整窗切分。没有这条约束时会退化成"同一断点处连续缩短的后缀级联"——
        实测某个 xlsx 缓冲（1241 token）产出 64 片、其中 60 片是 token 数 396→2 的递减后缀；
        某个 PDF 页（676 token）产出 15 片、其中 13 片由 14→2 递减。这些碎片既没有证据价值，
        也白占入库与嵌入预算。

        ``max_pieces`` 是**增量预算**：循环内每次追加后即比对，一旦超过就地抛
        ``ParseError(code="too_many_chunks")``。必须在生成过程中拦，而不是等返回之后再查
        ``len(pieces)``——重叠接近窗口时步长会塌到 1，切片数与总文本量随输入长度剧增，等
        全部生成完再检查等于没检查（实测 20M 字符输入在该配置下会在检查前先吃掉约 5 GB）。

        注意：本方法只保证"不产生同断点级联"和"覆盖完整"，**不禁止有意重叠**。当
        ``overlap`` 接近 ``max_tokens`` 时步长会很小、产出接近滑动窗口式的近似重复片段
        （步长下界为 ``max_tokens - 2*overlap``），这是该配置的固有代价，故默认值取 60/400。
        """
        check_split_params(max_tokens, overlap_tokens)
        if max_pieces is not None and max_pieces <= 0:
            raise piece_budget_error("本次切块", 0)
        text = text.strip()
        n = self.count(text)
        if n <= max_tokens:
            return [(text, n)]
        if self._tokenizer is None:
            return self._approx_split(text, max_tokens, overlap_tokens, max_pieces)

        enc = self._tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True, truncation=False
        )
        offsets = enc["offset_mapping"]
        total = len(enc["input_ids"])
        need = min_span(max_tokens, overlap_tokens)
        pieces: list[tuple[str, int]] = []
        start = 0
        while start < total:
            if self._budget_exceeded(pieces, max_pieces):
                raise piece_budget_error("本次切块", max_pieces)
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

    def _approx_split(self, text: str, max_tokens: int, overlap_tokens: int,
                      max_pieces: int | None = None) -> list[tuple[str, int]]:
        """mock/无 tokenizer 时的字符近似切分，规则与 ``split_long`` 一致（含增量预算）。"""
        window = max(16, max_tokens * 2)
        overlap_chars = max(0, overlap_tokens * 2)
        need = max(overlap_chars + 1, window - overlap_chars)
        pieces: list[tuple[str, int]] = []
        i = 0
        n_text = len(text)
        while i < n_text:
            if self._budget_exceeded(pieces, max_pieces):
                raise piece_budget_error("本次切块", max_pieces)
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
    max_pieces: int | None = None,
) -> list[Piece]:
    """把 (页码|段落, 文本) 单元流切成 Piece 列表。

    PDF：页码相同的单元可合并，切块不跨页（保证页码引用准确）。
    DOC/DOCX/TXT/MD：全部段落合并为一条流（引用记起始段落号）。

    ``max_pieces`` 是整份文档的切片预算，按剩余额度逐次下传给 ``split_long``：这样每个
    缓冲都在生成过程中就地受限，而不是等整份文档切完再查总数（后者在重叠接近窗口时会先
    吃掉大量内存）。
    """
    pieces_out: list[Piece] = []
    buffer_texts: list[str] = []
    buffer_page: int | None = None
    buffer_para: int | None = None
    buffer_tokens = 0

    def remaining() -> int | None:
        if max_pieces is None:
            return None
        left = max_pieces - len(pieces_out)
        if left <= 0:
            raise piece_budget_error("该文档", max_pieces)
        return left

    def flush() -> None:
        nonlocal buffer_texts, buffer_page, buffer_para, buffer_tokens
        merged = "\n".join(t for t in buffer_texts if t.strip()).strip()
        if merged:
            try:
                split = ta.split_long(merged, max_tokens, overlap_tokens, remaining())
            except ParseError as exc:
                if exc.code != "too_many_chunks" or max_pieces is None:
                    raise
                raise piece_budget_error("该文档", max_pieces) from exc
            for text, cnt in split:
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
