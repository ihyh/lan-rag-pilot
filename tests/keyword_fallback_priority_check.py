"""A filename fallback must retain stronger body evidence before bounded recall."""
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.index import VectorIndex


def main():
    failures = []
    checks = 0

    def check(condition, label, actual):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append((label, actual[:5] if isinstance(actual, list) else actual))

    # The model, shared title/body word, requested identifier, and recorded value
    # all vary. No business question, filename, or answer is embedded here.
    for model, title_word, identifier, value in (
        ("MOTOR-A", "Parameter", "AX7", 17),
        ("READER-Q", "Controller", "RF9", 43),
    ):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                         "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
        db.executemany("INSERT INTO documents VALUES (?,?,?)", [
            (1, "ready", f"{model} {title_word} guide.pdf"),
            (2, "ready", "OTHER guide.pdf"),
            (3, "ready", f"{model} {title_word} hidden.pdf"),
            (4, "failed", f"{model} {title_word} failed.pdf"),
        ])

        def add(cid, did, content, similarity):
            vec = np.array([similarity, np.sqrt(1 - similarity ** 2)], dtype=np.float32)
            db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, content, vec.tobytes()))

        # Both global AND and weak per-file retrieval can truncate the relevant
        # long chunk: short exact matches occur in the wrong file, while short
        # partial matches occur in the right file.
        for i in range(80):
            add(i + 1, 1, f"{identifier} nearby{i}", 0.80)
            add(i + 101, 2, f"{title_word} {identifier} unrelated{i}", 0.90)
        add(201, 1, f"{title_word} {identifier} value{value} "
            + " ".join(f"detail{i}" for i in range(100)), 0.95)
        add(202, 3, f"{title_word} {identifier} hidden", 0.999)
        add(203, 4, f"{title_word} {identifier} failed", 0.999)
        add(204, 1, "BODY_ONLY literal", 0.05)
        index = VectorIndex()
        index.reload(db)
        q = np.array([1, 0], dtype=np.float32)
        query = f"{model} {title_word} {identifier}"

        def search(text=query, scope=None, k=3, minimum=0.25):
            return index.search(q, k, document_ids={1, 2} if scope is None else scope,
                                min_score=minimum, query_text=text)

        for k in (1, 3, 60):
            for scope in ({1}, {1, 2}):
                hits = search(scope=scope, k=k, minimum=0.9999)
                check(bool(hits) and hits[0]["chunk_id"] == 201,
                      f"{model}: strict body AND precedes weaker filename fallback; k={k}, scope={scope}", hits)
                check(all(h["document_id"] == 1 for h in hits),
                      f"{model}: title-only restriction excludes wrong/hidden/failed files", hits)
        semantic = search(k=3)
        check(bool(semantic) and semantic[0]["chunk_id"] == 201,
              f"{model}: fusion retains strongest body match", semantic)
        expected_scores = {201: 0.95, 204: 0.05}
        expected_scores.update({i + 1: 0.80 for i in range(80)})
        expected_scores.update({i + 101: 0.90 for i in range(80)})
        check(all(h["chunk_id"] in expected_scores
                  and abs(h["score"] - expected_scores[h["chunk_id"]]) < 1e-6 for h in semantic),
              f"{model}: preserve raw cosine", semantic)
        check(search(scope=set()) == [], f"{model}: empty scope", None)
        outside = search(scope={2}, minimum=0.9999)
        check(bool(outside) and all(h["document_id"] == 2 for h in outside),
              f"{model}: fallback does not escape caller scope", outside)
        exact = search(text="BODY_ONLY", scope={1}, k=1, minimum=0.9999)
        check(bool(exact) and exact[0]["chunk_id"] == 204,
              f"{model}: ordinary exact body lookup unchanged", exact)
        ordinary = search(text="", scope={1})
        check(search(text="UNRECORDED_WORD", scope={1}) == ordinary,
              f"{model}: no known terms keeps semantic ordering", ordinary)
        # Removing the full conjunction must still leave useful filename
        # fallback hits. Losing the stronger match must not create an empty result.
        db.execute("DELETE FROM chunks WHERE id=201")
        index.reload(db)
        weak = search(k=3, minimum=0.9999)
        check(bool(weak) and all(1 <= h["chunk_id"] <= 80 for h in weak),
              f"{model}: no full conjunction retains existing fallback", weak)
        db.close()

    for label, actual in failures:
        print(f"FAIL {label}: {actual}")
    assert not failures, f"{len(failures)}/{checks} checks failed"
    print(f"keyword fallback priority: PASS ({checks} synthetic checks; no model calls)")


if __name__ == "__main__":
    main()
