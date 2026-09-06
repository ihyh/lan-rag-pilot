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
                "score": round(1 - cid / 1000, 4), "page": cid, "filename": "synthetic.txt"}

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
         patch.object(route.audit, "log_audit"):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        user = SimpleNamespace(id=1, username="synthetic")
        answer = route.query(QueryBody(question="巡检周期是多少？"), request, db, user)
        index.search.assert_called_once_with([1], 100, min_score=0.25)
        sent = model.call_args.args[1]
        assert len(sent) == 2 and sent[0]["content"] == full
        assert [s["chunk_id"] for s in answer["sources"]] == [s["chunk_id"] for s in sent]
        assert store.call_args.kwargs["sources"] == sent
        model.reset_mock()
        index.search.return_value = []
        answer = route.query(QueryBody(question="未知价格是多少？"), request, db, user)
        assert answer["sources"] == []
        model.assert_not_called()
    print("Retrieval quality check: PASS (terminal overlap, page boundaries, redundant tails, conflicting values, citation identity)")


if __name__ == "__main__":
    main()
