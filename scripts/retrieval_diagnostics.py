"""Run on the data host. Print metadata only; never write to the production DB."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-preview", action="store_true")
    parser.add_argument("--document-id", type=int)
    args = parser.parse_args()
    db = sqlite3.connect(settings.db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        report = {
            "feedback": [dict(row) for row in db.execute(
                "SELECT rating, COUNT(*) AS count FROM feedback GROUP BY rating"
            )],
            "review_cases": [dict(row) for row in db.execute(
                "SELECT c.id AS chat_id, c.status, c.latency_ms, "
                "COUNT(DISTINCT s.document_id) AS source_documents "
                "FROM chats c JOIN feedback f ON f.chat_id=c.id "
                "LEFT JOIN chat_sources s ON s.chat_id=c.id "
                "WHERE f.rating='unhelpful' GROUP BY c.id ORDER BY c.id DESC LIMIT 50"
            )],
            "gold_answers": "require human review; feedback is not ground truth",
        }
        if args.chunk_preview:
            doc_id = args.document_id
            if doc_id is None:
                row = db.execute(
                    "SELECT s.document_id FROM chat_sources s JOIN feedback f ON f.chat_id=s.chat_id "
                    "JOIN documents d ON d.id=s.document_id "
                    "WHERE f.rating='unhelpful' AND d.status='ready' "
                    "GROUP BY s.document_id ORDER BY COUNT(DISTINCT s.chat_id) DESC, s.document_id LIMIT 1"
                ).fetchone()
                if row is None:
                    raise ValueError("No ready document associated with negative feedback")
                doc_id = row[0]
            doc = db.execute("SELECT stored_name FROM documents WHERE id=? AND status='ready'", (doc_id,)).fetchone()
            if doc is None:
                raise ValueError("Document is not ready")
            from transformers import AutoTokenizer
            from app.chunking import TokenizerAdapter, chunk_units
            from app.parsing import EXTENSIONS, PARSERS

            upload_root = settings.upload_dir.resolve()
            path = (upload_root / doc["stored_name"]).resolve()
            if path.parent != upload_root:
                raise ValueError("Invalid stored document path")
            tokenizer = AutoTokenizer.from_pretrained(
                settings.embed_model, cache_dir=str(settings.models_dir), local_files_only=True
            )
            units = PARSERS[EXTENSIONS[path.suffix.lower()]](path)
            pieces = chunk_units(units, TokenizerAdapter(tokenizer), settings.chunk_max_tokens, settings.chunk_overlap_tokens)
            old_counts = [r[0] for r in db.execute("SELECT token_count FROM chunks WHERE document_id=?", (doc_id,))]
            def summary(counts):
                return {"chunks": len(counts), "short_le_60": sum(n <= 60 for n in counts),
                        "tokens_total": sum(counts), "max_tokens": max(counts, default=0)}
            report["chunk_preview"] = {"document_id": doc_id, "units": len(units),
                                       "before": summary(old_counts),
                                       "candidate": summary([p.token_count for p in pieces]),
                                       "production_changed": False}
            # Compare coverage within each page without emitting or storing source text.
            missing = 0
            for page in {unit.page for unit in units}:
                original = "".join("".join(unit.text.split()) for unit in units if unit.page == page)
                covered = bytearray(len(original))
                cursor = 0
                for piece in (piece for piece in pieces if piece.page == page):
                    text = "".join(piece.text.split())
                    start = original.find(text, cursor)
                    if start < 0:
                        raise ValueError("Candidate piece could not be mapped to its original page")
                    covered[start:start + len(text)] = b"\x01" * len(text)
                    cursor = start
                missing += covered.count(0)
            report["chunk_preview"]["uncovered_nonspace_characters"] = missing
        print(json.dumps(report, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Parser/tokenizer exceptions can contain private paths or document excerpts.
        print(json.dumps({"error_type": type(exc).__name__, "production_changed": False}))
        raise SystemExit(1)
