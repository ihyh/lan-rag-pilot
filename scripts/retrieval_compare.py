"""Read-only failed-query replay; outputs IDs/counts/timing, never question or source text."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.embeddings import embedding_service
from app.index import VectorIndex, technical_terms
from app.routers.query import _select_sources, _stored_document_ids


def main():
    with closing(sqlite3.connect(settings.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        index = VectorIndex()
        started = time.perf_counter()
        index.reload(db)
        index_seconds = time.perf_counter() - started
        embedding_service._load_real()
        if embedding_service.state != "ready":
            raise RuntimeError("Embedding model not ready")
        cases = db.execute(
            "SELECT c.id, c.question, v.document_ids FROM chats c "
            "JOIN conversations v ON v.id=c.conversation_id WHERE EXISTS "
            "(SELECT 1 FROM feedback f WHERE f.chat_id=c.id AND f.rating='unhelpful') "
            "ORDER BY c.id DESC LIMIT 50"
        ).fetchall()
        print(json.dumps({"cases": len(cases), "index_seconds": round(index_seconds, 3)}), flush=True)
        for case in cases:
            qvec = embedding_service.embed_query(case["question"])
            scope = set(_stored_document_ids(case["document_ids"])) or None
            variants = {}
            # Replay each question independently to isolate retrieval effects.
            for label, query_text in (("vector", ""), ("hybrid", case["question"])):
                started = time.perf_counter()
                hits = index.search(qvec, 100, document_ids=scope,
                                    min_score=settings.min_relevance_score, query_text=query_text)
                by_id = {hit["chunk_id"]: hit for hit in hits}
                sources = []
                if hits:
                    rows = db.execute(
                        "SELECT id AS chunk_id,document_id,content FROM chunks WHERE id IN ("
                        + ",".join("?" for _ in hits) + ")", list(by_id)
                    ).fetchall()
                    texts = {row["chunk_id"]: row for row in rows}
                    sources = _select_sources([dict(texts[hit["chunk_id"]], score=hit["score"])
                                               for hit in hits if hit["chunk_id"] in texts], 5)
                terms = set(technical_terms(case["question"]))
                variants[label] = {
                    "chunk_ids": [source["chunk_id"] for source in sources],
                    "document_ids": [source["document_id"] for source in sources],
                    "exact_term_passages": sum(bool(terms) and terms <= set(technical_terms(source["content"]))
                                               for source in sources),
                    "retrieval_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            print(json.dumps({"chat_id": case["id"], "has_technical_terms": bool(technical_terms(case["question"])),
                              **variants, "gold_verified": False}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "production_changed": False}), flush=True)
        raise SystemExit(1)
