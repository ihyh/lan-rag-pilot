"""Offline evidence-set evaluation. Explicit DB/cases/output; never writes the corpus.

Private questions, sources and traces go only to the explicitly selected report.
This evaluates retrieval, not semantic answer truth. No generation is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def peak_memory_bytes():
    """OS process peak, including native tensor memory (not just Python allocations)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def journal(path, event):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_cases(path):
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and cases, "non-empty case array required"
    seen, groups = set(), {}
    for case in cases:
        assert case["id"] not in seen and case["question"].strip()
        seen.add(case["id"])
        assert case["split"] in {"development", "acceptance"}
        group = case["group"]
        assert group not in groups or groups[group] == case["split"], "fact group crosses splits"
        groups[group] = case["split"]
        assert isinstance(case["required_evidence"], list)
        for evidence in case["required_evidence"]:
            assert evidence["chunk_ids"] and all(type(n) is int for n in evidence["chunk_ids"])
    return cases


def evidence_metrics(case, candidates, selected):
    needed = case["required_evidence"]
    candidate_ids = {s["chunk_id"] for s in candidates}
    present = []
    for evidence in needed:
        alternatives = set(evidence["chunk_ids"])
        present.append(any(s["chunk_id"] in alternatives and all(
            quote in s["content"] for quote in evidence.get("quotes", [])) for s in selected))
    index_complete = case.get("index_evidence_complete", True)
    first = next((i for i, s in enumerate(candidates, 1) if any(
        s["chunk_id"] in e["chunk_ids"] for e in needed)), None)
    candidate_recall = sum(bool(candidate_ids & set(e["chunk_ids"])) for e in needed) / len(needed) if needed else 0.0
    final_recall = sum(present) / len(needed) if needed else 0.0
    complete = bool(needed) and index_complete and all(present)
    failures = list(case.get("upstream_failures", []))
    if needed and candidate_recall < 1:
        failures.append("candidate_not_recalled")
    if needed and index_complete and candidate_recall == 1 and not complete:
        failures.append("selection_lost_evidence")
    return {"candidate_recall": candidate_recall, "evidence_recall": final_recall,
            "evidence_complete": complete, "index_evidence_complete": index_complete,
            "mrr": 1 / first if first else 0.0, "failure_stages": list(dict.fromkeys(failures)),
            "fact_correct": None, "citation_supported": None,
            "answer_review": "not_run", "unanswerable": not bool(needed)}


def summarize(records):
    elapsed = sorted(r["retrieval_ms"] for r in records)
    indexed = [r for r in records if r["metrics"]["index_evidence_complete"] and not r["metrics"]["unanswerable"]]
    summary = {"questions": len(records), "complete": sum(r["metrics"]["evidence_complete"] for r in records),
               **{key: statistics.mean(r["metrics"][key] for r in records)
                  for key in ("candidate_recall", "evidence_recall", "mrr")},
               "p50_ms": statistics.median(elapsed),
               "p95_ms": elapsed[max(0, int(len(elapsed) * .95 + .999) - 1)],
               "index_complete_subset": sum(r["metrics"]["index_evidence_complete"] and not r["metrics"]["unanswerable"] for r in records),
               "indexed_subset_metrics": {"questions": len(indexed), "complete": sum(r["metrics"]["evidence_complete"] for r in indexed),
                                          **{key: statistics.mean(r["metrics"][key] for r in indexed) if indexed else None
                                             for key in ("candidate_recall", "evidence_recall", "mrr")}},
               "unanswerable": sum(r["metrics"]["unanswerable"] for r in records),
               "quality_claim": False}
    rerank_ms = sorted(r["rerank_ms"] for r in records if r.get("rerank_ms") is not None)
    if rerank_ms:
        summary["rerank_p50_ms"] = statistics.median(rerank_ms)
        summary["rerank_p95_ms"] = rerank_ms[max(0, int(len(rerank_ms) * .95 + .999) - 1)]
        summary["rerank_questions"] = len(rerank_ms)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, default=ROOT)
    parser.add_argument("--split", choices=("development", "acceptance"), required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--history-mode", choices=("legacy", "current", "auxiliary"), default="legacy")
    args = parser.parse_args()
    if args.out.suffix.lower() != ".json":
        parser.error("--out must be a new .json report; the .jsonl sibling preserves partial progress")
    cases = [case for case in load_cases(args.cases) if case["split"] == args.split]
    assert cases and 1 <= args.top_k <= 10
    if args.validate_only:
        print("Validated", len(cases), args.split, "cases; no DB/model calls")
        return
    assert not args.out.exists(), "refuse to overwrite earlier evidence"
    journal_path = args.out.with_suffix(".jsonl")
    assert not journal_path.exists(), "refuse to overwrite partial/failed earlier evidence"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    journal_path.touch(exist_ok=False)
    code_root = args.code_root.resolve()
    identity = {"cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                "corpus_sha256": hashlib.sha256(args.db.read_bytes()).hexdigest(),
                "code_sha256": {str(p.relative_to(code_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((code_root / "app").rglob("*.py"))},
                "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "split": args.split, "history_mode": args.history_mode, "top_k": args.top_k}
    journal(journal_path, {"event": "run_started", "identity": identity})
    sys.path.insert(0, str(args.code_root.resolve()))
    import inspect
    from app.config import settings
    from app.embeddings import embedding_service
    from app.index import VectorIndex
    from app.routers.query import _select_sources
    configuration = {name: getattr(settings, name, None) for name in (
        "embed_model", "embed_backend", "embed_device", "min_relevance_score",
        "general_lexical", "rerank_enabled", "rerank_model")}
    configuration["history_mode"] = args.history_mode
    model_manifest = Path(getattr(settings, "rerank_model", "")) / "RERANKER_SOURCE.json"
    if getattr(settings, "rerank_enabled", False) and model_manifest.is_file():
        configuration["rerank_manifest"] = json.loads(model_manifest.read_text(encoding="utf-8"))
    journal(journal_path, {"event": "configuration", "settings": configuration})
    index = VectorIndex()
    rows = []
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        reload_started = time.perf_counter()
        index.reload(db)
        reload_ms = (time.perf_counter() - reload_started) * 1000
        data = {int(r["chunk_id"]): dict(r) for r in db.execute(
            "SELECT c.id AS chunk_id,c.document_id,c.seq,c.page,c.paragraph,c.content,d.filename "
            "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.status='ready'")}
        embedding_service._load_real()
        if embedding_service.state != "ready":
            raise RuntimeError(embedding_service.message)
        for case in cases:
            started = time.perf_counter()
            journal(journal_path, {"event": "question_started", "id": case["id"]})
            history = case.get("previous_question")
            query = f"{history}\n{case['question']}" if history and args.history_mode == "legacy" else case["question"]
            vector = embedding_service.embed_query(query)
            trace = {"channels": {}, "baseline_without_trace": True}
            options = {"trace": trace} if "trace" in inspect.signature(index.search).parameters else {}
            if args.history_mode == "auxiliary" and history:
                options["auxiliary_vec"] = embedding_service.embed_query(f"{history}\n{case['question']}")
            embed_ms = (time.perf_counter() - started) * 1000
            hits = index.search(vector, min(200, args.top_k * 20), document_ids=set(case["scope"]) if case.get("scope") is not None else None,
                                min_score=settings.min_relevance_score, query_text=case["question"], **options)
            sources = [dict(data[h["chunk_id"]], score=h["score"]) for h in hits]
            if getattr(settings, "rerank_enabled", False):
                from app.reranking import reranker
                ranked = reranker.rank(case["question"], sources, trace)
                selection_started = time.perf_counter()
                selected = _select_sources(ranked, args.top_k, case["question"], preserve_ranking=True, trace=trace)
            else:
                selection_started = time.perf_counter()
                selected = _select_sources(sources, args.top_k, case["question"])
            for source in selected:
                if Path(source["filename"]).suffix.lower() == ".xlsx":
                    source["table_headers"] = index.table_headers_for(source["document_id"])
            trace.setdefault("timings_ms", {}).update(embedding=embed_ms, selection=(time.perf_counter() - selection_started) * 1000)
            selected_ids = {s["chunk_id"] for s in selected}
            trace["selected"] = [s["chunk_id"] for s in selected]
            trace.setdefault("selection", [{"chunk_id": s["chunk_id"], "selected": s["chunk_id"] in selected_ids,
                                   "reason": "legacy_selection" if s["chunk_id"] in selected_ids else "dedup_or_rank_budget"} for s in sources])
            rows.append({"id": case["id"], "question": case["question"], "previous_question": history,
                         "semantic_query": query, "trace": trace, "candidates": sources, "selected": selected,
                         "candidate_count": len(sources), "selected_count": len(selected),
                         "rerank_ms": (trace.get("rerank") or {}).get("latency_ms"),
                         "retrieval_ms": (time.perf_counter() - started) * 1000,
                         "metrics": evidence_metrics(case, sources, selected)})
            journal(journal_path, {"event": "question_completed", "record": rows[-1]})
            print(case["id"], "complete", rows[-1]["metrics"]["evidence_complete"], flush=True)
    report = {"schema": "retrieval-evidence-v1", "split": args.split,
              "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              "corpus_sha256": hashlib.sha256(args.db.read_bytes()).hexdigest(),
              "code_root": str(args.code_root), "model": settings.embed_model,
              "top_k": args.top_k, "summary": summarize(rows), "records": rows}
    report["identity"] = identity
    report["settings"] = configuration
    report["index_reload_ms"] = reload_ms
    report["peak_process_memory_bytes"] = peak_memory_bytes()
    report["history_mode"] = args.history_mode
    report["strategies"] = {name: getattr(settings, name, False) for name in ("general_lexical", "rerank_enabled", "history_auxiliary")}
    report["strategies"]["history_auxiliary"] = args.history_mode == "auxiliary"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    journal(journal_path, {"event": "run_completed", "summary": report["summary"]})
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
