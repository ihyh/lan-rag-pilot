"""分块器结构性质回归：不依赖人工答案集，也不需要下载模型。

为什么是"性质"而不是"期望输出"：级联缺陷不是某段文本切错了，而是**在满足条件的文本上
都会发生的结构性退化**（同一断点处连续缩短的后缀片段）。因此断言的是性质：

1. **无同断点级联**：不存在"相邻两片终点相同、后一片更短"的情况（= 同一断点处的后缀级联）。
   判据用**原文偏移**，不用文本包含关系：重复文本里"甲。甲。"这类短片段天然是长片段的后缀，
   用文本判据会误报。**有意重叠是允许的**——重叠会让后一片越过前一片的终点，不构成级联。
2. **持续前进**：后一片的起始偏移严格大于前一片。
3. **覆盖完整**：片段区间并集覆盖去空白后的全文，相邻不得有空洞。
4. **位置上界**：每片 token ≤ ``max_tokens``，且不出现与上一片完全相同的片段。

偏移类判据（1、2、3）只在**片段唯一的文本**上成立：定位靠子串查找，重复文本无法唯一确定
位置。因此文本分两类：唯一片段文本做偏移校验；全部文本做规模与重复校验。
规模上限按各路径自己的窗口/重叠单位计算（token 路径用 token，字符近似路径用字符），
只用于抓"级联式爆炸"（一段短文产出几百片）。

覆盖三条路径：``TokenizerAdapter(None)`` → ``_approx_split``（字符单位，mock 后端走这条）、
``TokenizerAdapter(CharacterTokenizer())`` 与 ``TokenizerAdapter(WordTokenizer())`` →
offsets 路径（后者模仿真实 tokenizer：空白是分隔符、标点独立成 token）。
本机存在离线 BGE 模型时，额外用真实 tokenizer 重放已复现的两种形态与真实文件。
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RAG_SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("RAG_ROOT_PASSWORD", "test-password")
os.environ.setdefault("DEEPSEEK_API_KEY", "ollama")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("RAG_EMBED_BACKEND", "mock")

from app.chunking import (  # noqa: E402
    TokenizerAdapter,
    check_split_params,
    chunk_units,
    min_span,
)
from app.parsing import Unit  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def skip(msg: str) -> None:
    SKIP.append(msg)
    print(f"  [SKIP] {msg}")


class CharacterTokenizer:
    """字符级 stand-in：与 tests/retrieval_quality_check.py 一致（1 token = 1 字符）。"""

    def encode(self, text, **kwargs):
        return list(range(len(text)))

    def __call__(self, text, **kwargs):
        return {"input_ids": self.encode(text),
                "offset_mapping": [(i, i + 1) for i in range(len(text))]}


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]")


class WordTokenizer:
    """词级 stand-in，模仿真实 tokenizer 的两个关键行为：

    - 空白是**分隔符**，不会成为任何 token 的最后一个字符（``_SENTENCE_END`` 里的 ``"\\n"``
      在真实路径上因此永远匹配不到，本测试正是在这个前提下验证级联已消除）；
    - 标点独立成 token（``.`` ``:`` ``(`` …），因此句末符会成为某个 token 的末字符。
    """

    def _spans(self, text: str) -> list[tuple[int, int]]:
        return [m.span() for m in _TOKEN_RE.finditer(text)]

    def encode(self, text, **kwargs):
        return list(range(len(self._spans(text))))

    def __call__(self, text, **kwargs):
        spans = self._spans(text)
        return {"input_ids": list(range(len(spans))), "offset_mapping": spans}


def path_geometry(ta: TokenizerAdapter, max_tokens: int, overlap: int,
                  source: str) -> tuple[int, int, int]:
    """返回 (窗口, 重叠, 文本单位数)，单位随路径而变。

    - 无 tokenizer → ``_approx_split``：单位是**字符**，窗口 ``max(16, max_tokens*2)``；
    - offsets 路径：单位是该 tokenizer 的 **token**。
    """
    if ta._tokenizer is None:  # noqa: SLF001 - 测试内需要区分两条路径
        return max(16, max_tokens * 2), max(0, overlap * 2), len(source)
    return max_tokens, overlap, ta.count(source)


def source_span(text: str) -> tuple[int, int]:
    stripped = text.strip()
    if not stripped:
        return 0, 0
    head = text.find(stripped)
    return head, head + len(stripped)


def locate(pieces: list[tuple[str, int]], source: str) -> list[tuple[int, int]] | None:
    """还原每片区间；任一 NOT 唯一的片段就放弃（重复文本无法唯一定位）。

    唯一性用 ``find == rfind`` 判定，而不是 ``str.count``：``count`` 不统计**重叠**出现，
    例如 ``"甲"*800`` 在 1001 个"甲"组成的文本里 ``count == 1``，但那 201 个位置都能匹配，
    定位会全部落到第一处，从而伪造出"原文未覆盖"。
    """
    for text, _ in pieces:
        if source.find(text) != source.rfind(text):
            return None
    spans: list[tuple[int, int]] = []
    cursor = 0
    for text, _ in pieces:
        at = source.find(text, cursor)
        if at < 0:
            return None
        spans.append((at, at + len(text)))
        cursor = at  # 允许重叠：下一次从本次起点继续找
    return spans


def verify(label: str, source: str, pieces: list[tuple[str, int]], max_tokens: int,
           window: int, overlap: int, units: int, collect: list[str],
           token_slack: int = 0) -> str:
    """校验全部性质；返回 "offsets"（做了偏移校验）或 "scale"（仅规模校验）。

    ``token_slack`` 只给无 tokenizer 的近似路径用：``_approx_count`` 的取整
    （``(cjk+1)//2 + (other+3)//4``）会让 32 字符的混合片段算出 17 而窗口是 16，属该路径的
    已知近似误差（mock 后端仅供测试，文档已写明不可上线），因此按余量校验而不是放宽全局。
    """
    def bad(msg: str) -> None:
        collect.append(f"{label}：{msg}")

    if not pieces:
        bad("切分结果为空")
        return "scale"
    if any(not text.strip() for text, _ in pieces):
        bad("存在空白片段")
    over = [n for _, n in pieces if n > max_tokens + token_slack]
    if over:
        bad(f"有 {len(over)} 片超过 token 上界 {max_tokens}"
            + (f"（含近似余量 {token_slack}）" if token_slack else ""))
    limit = math.ceil(units / max(1, window - 2 * overlap)) + 2
    if len(pieces) > limit:
        bad(f"片段数 {len(pieces)} 超出规模上限 {limit}（疑似级联式爆炸）")

    spans = locate(pieces, source)
    if spans is None:
        return "scale"
    stride_min = max(1, window - 2 * overlap)
    if stride_min >= 2:
        if not all(b[0] > a[0] for a, b in zip(spans, spans[1:])):
            bad("存在未前进的相邻片段")
    else:
        # 步长为 1 时，切片首部空白被 strip 掉会让相邻两片的起点相同（不是缺陷）；
        # 此时只能要求不倒退，前进性由"无同断点级联"和规模上限共同保证。
        if not all(b[0] >= a[0] for a, b in zip(spans, spans[1:])):
            bad("片段起点出现倒退")
    # 非空白覆盖：片段经 strip() 处理，纯空白窗口会被丢弃或缩短，因此空洞内容只允许是空白。
    missing = [source[:spans[0][0]]]
    missing += [source[a[1]:b[0]] for a, b in zip(spans, spans[1:]) if b[0] > a[1]]
    missing.append(source[spans[-1][1]:])
    leaked = [m for m in missing if m.strip()]
    if leaked:
        bad(f"非空白原文未被覆盖：{len(leaked)} 处，例如 {leaked[0][:30]!r}")
    non_ws = [i for i, ch in enumerate(source) if not ch.isspace()]
    if non_ws:
        if spans[0][0] > non_ws[0]:
            bad(f"首个非空白字符（偏移 {non_ws[0]}）未被任何片段覆盖")
        if spans[-1][1] < non_ws[-1] + 1:
            bad(f"最后一个非空白字符（偏移 {non_ws[-1]}）未被任何片段覆盖")
    # 同断点级联的精确判据：连续 ≥3 片共尾、逐片为前一片的**文本后缀**、且逐片更短。
    # 只看"共尾"会把 strip 造成的度量假象（步长 1-2 的滑动窗口）误判成级联；
    # 只看"文本后缀"会把周期文本的尾片误判成级联。两个条件同时成立并连续出现才是缺陷。
    run = longest = 0
    for (a, pa), (b, pb) in zip(zip(spans, pieces), zip(spans[1:], pieces[1:])):
        if a[1] == b[1] and len(pb[0]) < len(pa[0]) and pa[0].endswith(pb[0]):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    if longest >= 2:
        bad(f"存在同断点级联：连续 {longest + 1} 片共尾且逐片缩短为前片后缀")
    return "offsets"


SHAPES_UNIQUE = {
    "每词一行（PDF 形态）": "Fortrend Communication Manual Revision 5.\n"
                            + "\n".join(f"code{i:04d}" for i in range(400)),
    "时间戳日志（xlsx 形态）": "".join(
        f"09:{40 + i % 15}:{10 + i % 40}.{i:03d} Rcv : 0{i % 8}\n" for i in range(200)),
    "编号长文（无句末符）": " ".join(f"tok{i:05d}" for i in range(400)),
}
SHAPES_ANY = {
    "无标点重复长文": "甲" * 400 + "乙" * 100,
    "单个早断点": "。" + "甲" * 1000,
    "早断点+长无标点段": "甲" * 10 + "。" + "乙" * 990 + "。" + "丙" * 300,
    "中文句密": "甲。" * 600,
}


def run_grid(ta: TokenizerAdapter, path_label: str, token_slack: int = 0) -> tuple[int, int, int]:
    """参数 × 形态网格；返回 (组合数, 做偏移校验的组合数, 失败数)。"""
    failures: list[str] = []
    combos = offset_checked = 0
    for max_tokens in (16, 64, 400):
        for overlap in sorted({0, 1, max(1, max_tokens // 4), max_tokens - 1}):
            if overlap >= max_tokens:
                continue
            for shape, text in {**SHAPES_UNIQUE, **SHAPES_ANY}.items():
                pieces = ta.split_long(text, max_tokens, overlap)
                window, ov, units = path_geometry(ta, max_tokens, overlap, text)
                label = f"{path_label}｜{shape}｜max={max_tokens},overlap={overlap}"
                mode = verify(label, text, pieces, max_tokens, window, ov, units, failures,
                              token_slack=token_slack)
                combos += 1
                offset_checked += (mode == "offsets")
    if failures:
        FAIL.extend(failures)
        print(f"  [FAIL] {path_label}：{len(failures)} 项不满足")
        for item in failures[:8]:
            print(f"      - {item}")
    else:
        PASS.append(f"{path_label}：{combos} 组参数×形态全部满足性质")
        print(f"  [PASS] {path_label}：{combos} 组参数×形态全部满足性质"
              f"（其中 {offset_checked} 组做了完整偏移校验，"
              f"{combos - offset_checked} 组为重复文本、仅规模校验）")
    return combos, offset_checked, len(failures)


def run_page_attribution(ta: TokenizerAdapter, path_label: str) -> None:
    """页码归属：切块不跨页（PDF 引用准确性的前提）。"""
    units = [Unit("甲" * 500, page=1), Unit("乙" * 500, page=2), Unit("丙" * 200, page=3)]
    pages = [p.page for p in chunk_units(units, ta, 200, 40)]
    check(all(p in (1, 2, 3) for p in pages), f"{path_label}：每片都带页码")
    check(pages == sorted(pages), f"{path_label}：页码随原文顺序不倒退（{pages[:6]}…）")
    check(set(pages) == {1, 2, 3}, f"{path_label}：三个单元都产出了片段")
    pieces = chunk_units(units, ta, 200, 40)
    by_marker: dict[str, set[int]] = {}
    for p in pieces:
        by_marker.setdefault(p.text[0], set()).add(p.page)
    check(all(len(v) == 1 for v in by_marker.values()), f"{path_label}：同一片不跨页")
    check(all(p.token_count <= 200 for p in pieces),
          f"{path_label}：跨单元切块仍守住 token 上界")


def run_real_tokenizer_replay() -> None:
    """真实 BGE tokenizer 可用时，重放已复现的两种真实形态与真实文件。"""
    model_dir = os.environ.get("CHUNK_CHECK_MODEL") or r"C:/rag-personal/models/bge-small-zh-v1.5"
    if not Path(model_dir).exists():
        skip(f"真实 tokenizer 重放：未找到本地模型目录 {model_dir}（CI 上属预期）")
        return
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        from transformers import AutoTokenizer

        tok = AutoTokenizer.from_pretrained(model_dir)
    except Exception as exc:  # noqa: BLE001
        skip(f"真实 tokenizer 重放：加载失败（{exc.__class__.__name__}: {exc}）")
        return
    ta = TokenizerAdapter(tok)
    failures: list[str] = []

    replay = {
        "每词一行（PDF 形态）": "Fortrend Confidential Document Serial (ASCII) and Parallel "
                              "Communication Manual Revision 5.\n"
                              + "\n".join(f"code{i:04d}" for i in range(700)),
        "时间戳日志（xlsx 形态）": "".join(
            f"09:{40 + i % 15}:{10 + i % 40}.{i:03d} Rcv : 0{i % 8} 6D {i:02X}\n"
            for i in range(400)),
    }
    for label, text in replay.items():
        pieces = ta.split_long(text, 400, 60)
        window, ov, units = path_geometry(ta, 400, 60, text)
        mode = verify(f"真实 tokenizer｜{label}", text, pieces, 400, window, ov, units, failures)
        check(True, f"真实 tokenizer｜{label}：{len(pieces)} 片，"
                    f"最短 {min(n for _, n in pieces)} token，校验方式={mode}")

    uploads = Path(os.environ.get("CHUNK_CHECK_UPLOADS") or r"C:\rag-personal\data\uploads")
    from app.parsing import PARSERS

    for label, name, kind in (
        ("PDF 原文件", "141b731ddfdd4df7bc0723f584b9fe45.pdf", "pdf"),
    ):
        path = uploads / name
        if not path.exists():
            skip(f"真实文件重放：{name} 不存在（本机专属样本，CI 上属预期）")
            continue
        total = worst = 0
        for unit in PARSERS[kind](path):
            if not (unit.text or "").strip():
                continue
            pieces = ta.split_long(unit.text, 400, 60)
            total += len(pieces)
            worst = max(worst, len(pieces))
            window, ov, units = path_geometry(ta, 400, 60, unit.text)
            verify(f"真实文件｜{label}｜page={unit.page}", unit.text, pieces, 400,
                   window, ov, units, failures)
        check(True, f"真实文件｜{label}：共 {total} 片，单页最多 {worst} 片")

    if failures:
        FAIL.extend(failures)
        print(f"  [FAIL] 真实 tokenizer 重放：{len(failures)} 项不满足")
        for item in failures[:8]:
            print(f"      - {item}")


def main() -> None:
    print("\n== 参数区间校验（0 <= overlap < max_tokens）==")
    for bad_max in (0, -1):
        try:
            check_split_params(bad_max, 0)
            check(False, f"max_tokens={bad_max} 应被拒绝")
        except ValueError:
            check(True, f"max_tokens={bad_max} 被拒绝")
    for bad_overlap in (-1, 400, 401):
        try:
            check_split_params(400, bad_overlap)
            check(False, f"overlap={bad_overlap} 应被拒绝")
        except ValueError:
            check(True, f"overlap={bad_overlap}（max=400）被拒绝")
    for ok in (0, 1, 60, 399):
        check_split_params(400, ok)
    check(True, "overlap=0/1/60/399（max=400）均被接受")
    check(min_span(400, 60) == 340, f"min_span(400,60)=340（实际 {min_span(400, 60)}）")
    check(min_span(400, 399) == 400, f"min_span(400,399)=400（实际 {min_span(400, 399)}）")
    check(min_span(400, 201) == 202, f"min_span(400,201)=202（实际 {min_span(400, 201)}）")
    check(min_span(100, 20) == 80, f"min_span(100,20)=80（实际 {min_span(100, 20)}）")

    print("\n== 字符近似路径（无 tokenizer：mock 后端走这条）==")
    run_grid(TokenizerAdapter(), "字符近似路径", token_slack=1)
    run_page_attribution(TokenizerAdapter(), "字符近似路径")

    print("\n== offsets 路径 A：字符级 stand-in ==")
    run_grid(TokenizerAdapter(CharacterTokenizer()), "offsets/字符级")
    run_page_attribution(TokenizerAdapter(CharacterTokenizer()), "offsets/字符级")

    print("\n== offsets 路径 B：词级 stand-in（CI 覆盖真实分支）==")
    run_grid(TokenizerAdapter(WordTokenizer()), "offsets/词级")
    run_page_attribution(TokenizerAdapter(WordTokenizer()), "offsets/词级")

    print("\n== 断点太近时不允许产出近乎空的片段 ==")
    ta = TokenizerAdapter(WordTokenizer())
    source = "word " * 5 + ". " + " ".join(f"pad{i:04d}" for i in range(300))
    pieces = ta.split_long(source, 100, 20)
    check(len(pieces) >= 2, f"该样本应产生多片（实际 {len(pieces)}）")
    if len(pieces) >= 2:
        shortest = min(n for _, n in pieces[:-1])  # 尾片可以短，是末尾余量
        check(shortest >= min_span(100, 20),
              f"除尾片外每片至少 {min_span(100, 20)} token（实测最短 {shortest}）")

    print("\n== 真实 BGE tokenizer 重放（本机有模型时执行）==")
    run_real_tokenizer_replay()

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败"
          + (f", {len(SKIP)} 跳过" if SKIP else ""))
    if FAIL:
        print("失败项（前 20 条）:")
        for item in FAIL[:20]:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
