"""Synthetic regressions only: no model downloads, production DB or private documents."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["RAG_EMBED_BACKEND"] = "mock"
os.environ["RAG_SECRET_KEY"] = "test-secret"

from app.chunking import TokenizerAdapter, chunk_units
from app.parsing import Unit


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return list(range(len(text)))

    def __call__(self, text, **kwargs):
        return {"input_ids": self.encode(text),
                "offset_mapping": [(i, i + 1) for i in range(len(text))]}


def main():
    # Existing CI entry point also runs the exact-term regression suite.
    from hybrid_retrieval_check import main as check_hybrid
    check_hybrid()
    ta = TokenizerAdapter(CharacterTokenizer())
    text = "甲" * 400 + "乙" * 100
    pieces = ta.split_long(text, 400, 60)
    assert len(pieces) == 2, f"terminal overlap produced {len(pieces)} pieces, expected 2"
    assert pieces == [(text[:400], 400), (text[340:], 160)]
    for text in ("甲" * 300 + "。" + "乙" * 300, "甲。" * 600, "甲" * 1100):
        pieces = ta.split_long(text, 400, 60)
        assert all(n <= 400 for _, n in pieces)
        assert len(pieces) <= 5
        assert pieces[-1][0].endswith(text[-60:])
    pieces = chunk_units([Unit("甲" * 500, page=1), Unit("乙" * 500, page=2)], ta, 400, 60)
    assert [p.page for p in pieces] == [1, 1, 2, 2]

    from app.routers.query import _select_sources

    def source(cid, text, doc=1):
        return {"chunk_id": cid, "document_id": doc, "content": text,
                "score": round(1 - cid / 1000, 4), "page": cid, "seq": cid,
                "filename": "synthetic.txt"}

    full = "测试平台的巡检周期为每周一次。运行范围是10至80，调整后点击确认。"
    candidates = [source(i, full[-(i + 1):]) for i in range(1, 61)]
    candidates += [source(61, full), source(62, "测试平台应在维护页面修改巡检周期。")]
    selected = _select_sources(candidates, 5)
    assert len(selected) == 2, selected
    assert selected[0]["content"] == full
    assert selected[1]["chunk_id"] == 62
    assert all(s in candidates for s in selected), "citations must retain original source fields"
    assert len(_select_sources([source(1, "范围10至80"), source(2, "范围10至90")], 5)) == 2
    assert len(_select_sources([source(1, full), source(2, full, doc=2)], 5)) == 2
    assert len(_select_sources([source(1, full), source(2, full)], 5)) == 1

    overlap = "请求状态数据后设备回复状态信息，以下内容属于同一页连续说明。"
    anchor = dict(source(200, "协议说明开头。" + overlap, doc=3), page=6, seq=10)
    continuation = dict(source(203, overlap + "设备回复ID为0x61。", doc=3), page=6, seq=11)
    distractors = [source(201, "其它设备状态说明。", doc=4), source(202, "另一协议状态说明。", doc=5)]
    continued = _select_sources([anchor, *distractors, continuation], 3, "设备回复状态ID是什么？")
    assert [item["chunk_id"] for item in continued] == [200, 203, 201], continued
    no_overlap = dict(continuation, chunk_id=204, content="同页但不是重叠续段。")
    assert 204 not in [item["chunk_id"] for item in
                       _select_sources([anchor, *distractors, no_overlap], 3, "状态ID？")], \
        "无切片重叠的相邻行不得挤掉原Top-K"
    other_page = dict(continuation, chunk_id=205, page=7)
    assert 205 not in [item["chunk_id"] for item in
                       _select_sources([anchor, *distractors, other_page], 3, "状态ID？")], \
        "跨页相邻行不得作为同页续段提升"

    # Correct evidence can be inside the bounded pool but displaced by another device/row.
    other = dict(source(80, "参考版本为1.7。", doc=2), filename="MODEL-20 测试.xlsx")
    target = dict(source(81, "参考版本为5.2.7。"), filename="MODEL-10 测试.xlsx")
    question = "MODEL-10 的参考版本是什么？"
    assert _select_sources([other, target], 1, question)[0] is target
    assert _select_sources([other, target], 2, question) == [target, other], \
        "model preference must not discard different documents or rewrite citations"
    assert _select_sources([other, target], 1, "MODEL-100 的参考版本？")[0] is other, \
        "model token matching must not use prefixes"
    assert _select_sources([other, target], 1, "P20 参数是什么意思？")[0] is other, \
        "a parameter is not a filename model discriminator"

    wrong_rows = [source(82, "工作表第53行 A53=翻转通道46；日志53"),
                  source(83, "翻转通道530：05 FF"), source(84, "翻转通道153：05 FF"),
                  source(92, "通道53.5：05 FF"), source(93, "通道53A：05 FF"),
                  source(94, "通道53至63：05 FF")]
    channel = source(85, "翻转通道53：发送05 35两次")
    assert _select_sources(wrong_rows + [channel], 1, "翻转通道 53 的指令？")[0] is channel
    assert _select_sources(wrong_rows, 3, "通道53？") == wrong_rows[:3]
    assert _select_sources(wrong_rows + [channel], 1, "通道0至63的范围？")[0] is wrong_rows[0]
    assert _select_sources(wrong_rows + [channel], 1, "测试日志53？")[0] is wrong_rows[0]

    operation = dict(source(86, "工作表《测试》第 3 行：B3=关闭CLOSE互锁指令功能；D3=AUTO"),
                     filename="MODEL-10 测试.xlsx")
    alternative = dict(source(87, "工作表《测试》第 4 行：B4=执行CLOSE操作；D4=REMOTE"),
                       filename="MODEL-10 测试.xlsx")
    question = "MODEL-10 关闭 CLOSE 互锁指令功能的前置条件？"
    assert _select_sources([alternative, operation], 1, question)[0] is operation
    assert _select_sources([operation, alternative], 1,
                           "MODEL-10 执行CLOSE操作的前置条件？")[0] is alternative
    plain = dict(operation, filename="MODEL-10.txt")
    assert _select_sources([alternative, plain], 1, question)[0] is alternative, \
        "cell syntax outside XLSX must not gain spreadsheet priority"
    premise = dict(source(88, "前提为E84 AUTO MODE。"), filename="MODEL-10 测试.xlsx")
    auto = dict(source(89, "工作表《测试》第54行：C54=开启E84本地模式；E54=SET_ACCESS_MODE_AUTO"),
                filename="MODEL-10 测试.xlsx")
    context = dict(source(90, "E84协议说明。"), filename="MODEL-10 测试.xlsx")
    manual = dict(source(91, "工作表《测试》第55行：C55=开启E84本地模式\nHost->MODEL；E55=SET_ACCESS_MODE_MANUAL"),
                  filename="MODEL-10 测试.xlsx")
    mode_sources = _select_sources([premise, auto, context, manual], 3,
                                  "MODEL-10 E84 AUTO MODE下开启E84本地模式用什么指令？")
    assert mode_sources == [auto, premise, context], \
        "a multiline cell prefix must not promote an opposite-mode instruction"
    assert _select_sources([source(1, "  \n")], 5) == []
    assert _select_sources(candidates, 1) == selected[:1]

    # Exercise the route as well: dedup before the model, unchanged relevance gate,
    # original source IDs in both the saved chat and the response, no call on no-match.
    from app.routers import query as route
    from app.schemas import QueryBody
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = [dict(s, paragraph=None) for s in candidates]
    model = MagicMock(return_value={"answer": "每周一次。[1]", "model": "mock", "latency_ms": 1,
                                   "prompt_tokens": 10, "completion_tokens": 5})
    index = MagicMock()
    index.size.return_value = len(candidates)
    index.search.return_value = candidates
    with patch.object(route, "vector_index", index), \
         patch.object(route, "embedding_service", SimpleNamespace(state="ready", embed_query=lambda q: [1])), \
         patch.object(route, "settings", SimpleNamespace(min_relevance_score=0.25, embed_backend="st")), \
         patch.object(route.rt, "get_all", return_value={"queries_per_minute": 10, "top_k": 5}), \
         patch.object(route.query_limiter, "allow", return_value=(True, 0)), \
         patch.object(route, "llm_gate", MagicMock()), \
         patch.object(route, "llm_chat", model), \
         patch.object(route, "_store_chat", return_value=(1, 1)) as store, \
         patch.object(route.audit, "log_audit"), \
         patch.object(route.acl, "effective_scope",
                      side_effect=lambda _db, _u, requested: set(requested) if requested else None):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        user = SimpleNamespace(id=1, username="synthetic")
        answer = route.query(QueryBody(question="巡检周期是多少？"), request, db=db, user=user)
        index.search.assert_called_once_with([1], 100, document_ids=None, min_score=0.25, query_text="巡检周期是多少？")
        sent = model.call_args.args[1]
        assert len(sent) == 2 and sent[0]["content"] == full
        assert [s["chunk_id"] for s in answer["sources"]] == [s["chunk_id"] for s in sent]
        assert store.call_args.kwargs["sources"] == sent
        scoped = [source(70, "限定设备的巡检周期为每月一次。", doc=2)]
        db.execute.return_value.fetchall.return_value = [dict(scoped[0], paragraph=None)]
        index.search.return_value = scoped
        index.reset_mock()
        model.reset_mock()
        store.reset_mock()
        with patch.object(route, "_require_ready_documents") as require_ready:
            answer = route.query(
                QueryBody(question="限定设备多久巡检？", document_ids=[2]), request, db=db, user=user
            )
        require_ready.assert_called_once_with(db, [2], user)
        index.search.assert_called_once_with([1], 100, document_ids={2}, min_score=0.25, query_text="限定设备多久巡检？")
        assert [source["document_id"] for source in answer["sources"]] == [2]
        assert store.call_args.kwargs["document_ids"] == [2]
        model.reset_mock()
        index.search.return_value = []
        answer = route.query(QueryBody(question="未知价格是多少？"), request, db=db, user=user)
        assert answer["sources"] == []
        model.assert_not_called()
    print("Retrieval quality check: PASS (terminal overlap, continuation coverage, redundant tails, conflicting values, citation identity)")


if __name__ == "__main__":
    main()
