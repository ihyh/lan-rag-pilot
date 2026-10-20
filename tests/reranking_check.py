"""Rerank windows/selection contracts without downloading or mocking accuracy."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.reranking import Reranker, RerankUnavailable
from app.routers.query import _select_sources


class Tokenizer:
    def __call__(self, text, **kwargs):
        return {"input_ids": list(range(len(text))), "offset_mapping": [(i, i + 1) for i in range(len(text))]}
    def num_special_tokens_to_add(self, pair):
        return 4


class Model:
    tokenizer = Tokenizer()
    def predict(self, pairs, batch_size, show_progress_bar):
        assert batch_size == 4 and not show_progress_bar
        assert all(len(question) + len(passage) + 4 <= 512 for question, passage in pairs)
        return [9.0 if "needle" in passage else 1.0 for _, passage in pairs]


def source(cid, content, doc=1, **kwargs):
    return dict(chunk_id=cid, document_id=doc, seq=cid, filename="manual.txt", page=1,
                content=content, score=.12 + cid / 1000, **kwargs)


def route_contracts():
    from app.routers import query as route
    from app.main import create_app
    service = Reranker()
    service._model = Model()
    db, index, model = MagicMock(), MagicMock(), MagicMock()
    items = [dict(source(1, "other"), paragraph=None), dict(source(2, "needle target"), paragraph=None)]
    db.execute.return_value.fetchall.return_value = items
    index.size.return_value = 2
    index.search.return_value = items
    result = {"answer": "target [1]", "model": "mock", "latency_ms": 1,
              "prompt_tokens": 10, "completion_tokens": 3}
    model.return_value = result
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[route.get_db] = lambda: db
    app.dependency_overrides[route.require_user] = lambda: SimpleNamespace(id=1, username="test")
    with patch.object(settings, "rerank_enabled", True), patch.object(settings, "history_auxiliary", True), \
         patch.object(route, "reranker", service), patch.object(route, "vector_index", index), \
         patch.object(route, "embedding_service", SimpleNamespace(state="ready", embed_query=lambda q: [1])), \
         patch.object(route, "_require_ready_documents"), patch.object(route.acl, "effective_scope", return_value={1}), \
         patch.object(route.rt, "get_all", return_value={"top_k": 1, "queries_per_minute": 100}), \
         patch.object(route.query_limiter, "allow", return_value=(True, 0)), \
         patch.object(route, "llm_gate", MagicMock()), patch.object(route, "llm_chat", model), \
         patch.object(route, "_store_chat", return_value=(1, 1)) as store, \
         patch.object(route.audit, "log_audit"), patch.object(route, "connect", return_value=db), \
         patch.object(route, "llm_stream_chat", return_value=iter([
             {"type": "delta", "text": "target [1]"},
             {"type": "usage", "model": "mock", "latency_ms": 1, "prompt_tokens": 10, "completion_tokens": 3}])):
        client = TestClient(app)
        answer = client.post("/api/query", json={"question": "target?", "document_ids": [1]})
        assert answer.status_code == 200, answer.text
        assert set(answer.json()["sources"][0]) == {"chunk_id", "document_id", "filename", "page", "paragraph", "score"}
        assert answer.json()["sources"][0]["chunk_id"] == 2
        assert model.call_args.args[1][0]["content"] == "needle target"
        assert index.search.call_args.kwargs["document_ids"] == {1}
        stream = client.post("/api/query?stream=true", json={"question": "target?", "document_ids": [1]})
        events = [json.loads(line) for line in stream.text.splitlines()]
        assert stream.status_code == 200 and [e["type"] for e in events] == ["delta", "done"]
        assert store.call_args.kwargs["sources"][0]["chunk_id"] == 2
        with patch.object(service, "rank", side_effect=RerankUnavailable("model unavailable")):
            for streamed in (False, True):
                model.reset_mock()
                store.reset_mock()
                failure = client.post(f"/api/query?stream={str(streamed).lower()}", json={"question": "target?"})
                assert failure.status_code == 503 and failure.json()["detail"]["code"] == "rerank_unavailable"
                model.assert_not_called()
                store.assert_not_called()
    # No lifespan entered: these readiness checks never bootstrap or write a DB.
    import app.main as main
    with patch.object(settings, "rerank_enabled", True), \
         patch.object(main, "embedding_service", SimpleNamespace(state="ready", message="", device="cpu")), \
         patch.object(main, "reranker", SimpleNamespace(state="error", message="model unavailable")), \
         patch.object(main.llm_probe, "probe") as probe:
        ready = TestClient(create_app()).get("/api/ready")
        assert ready.status_code == 503 and ready.json()["reason"] == "rerank_not_ready"
        probe.assert_not_called()


def main():
    service = Reranker()
    with patch.object(settings, "rerank_model", "absent-model-do-not-download"):
        try:
            service.rank("query", [source(1, "text")])
        except RerankUnavailable as exc:
            assert "未静默回退" in str(exc)
        else:
            raise AssertionError("missing enabled model silently succeeded")
    service._model = Model()
    long = "a" * 1200 + "needle tail"
    items = [source(1, "unrelated"), source(2, long)]
    trace = {}
    ranked = service.rank("query", items, trace)
    assert [s["chunk_id"] for s in ranked] == [2, 1]
    assert ranked[0]["content"] == long and ranked[0]["score"] == items[1]["score"]
    assert not any("rerank_score" in s for s in items)
    spans = [w for w in trace["rerank"]["windows"] if w["chunk_id"] == 2]
    assert spans[0]["start"] == 0 and spans[-1]["end"] == len(long)
    assert all(a["end"] >= b["start"] for a, b in zip(spans, spans[1:]))
    assert len(spans) > 1
    duplicates = [source(1, "same"), source(2, "same"), source(3, "same", doc=2), source(4, "same; unless OFF")]
    duplicate_trace = {}
    assert len(service.rank("query", duplicates, duplicate_trace)) == 3
    assert duplicate_trace["rerank"]["exact_duplicates_removed"] == [2]
    assert len(duplicate_trace["rerank"]["windows"]) == 3, "duplicate was scored before removal"
    with patch.object(service._model, "predict", return_value=[float("nan")] * 4):
        try:
            service.rank("query", items)
        except RerankUnavailable:
            pass
        else:
            raise AssertionError("invalid scores silently accepted")
    ranked = [dict(source(1, "target text"), filename="BZ7.txt"),
              dict(source(2, "AX9 field", doc=2), filename="AX9.txt")]
    selected = _select_sources(ranked, 1, "AX9 field?", preserve_ranking=True)
    assert selected[0]["chunk_id"] == 1, "legacy heuristic overrode rerank relevance"
    conflicts = [source(1, "value11 when READY"), source(2, "value11 when READY; not when OFF")]
    assert len(_select_sources(conflicts, 3, preserve_ranking=True)) == 2
    assert _select_sources([source(1, " \n")], 3, preserve_ranking=True) == []
    assert len(_select_sources([source(1, "same"), source(2, "same"), source(3, "same", doc=2)], 3, preserve_ranking=True)) == 2
    overlap = "complete evidence overlap " * 2
    continuation = [source(1, "start " + overlap), source(2, overlap + "end"), source(3, "another")]
    notes = {}
    assert [s["chunk_id"] for s in _select_sources(continuation, 2, preserve_ranking=True, trace=notes)] == [1, 2]
    assert notes["selection"][1]["reason"] == "verified_continuation"
    other_page = dict(continuation[1], page=2)
    assert [s["chunk_id"] for s in _select_sources([continuation[0], continuation[2], other_page], 2,
                                                 preserve_ranking=True)] == [1, 3], "blind neighboring-page fill"
    assert not settings.rerank_enabled and not settings.general_lexical and not settings.history_auxiliary
    route_contracts()
    print("Reranker missing-model, full-window coverage, finite scores and conservative ranked selection passed.")


if __name__ == "__main__":
    main()
