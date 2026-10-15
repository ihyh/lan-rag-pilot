"""PDF-split SECS headers retain exact body recall without inventing shorter IDs."""
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.index import VectorIndex


def main():
    failures = []
    checks = 0

    def check(condition, label, actual=None):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append((label, actual))

    for model, field, value, split, canonical in (
        ("ARM-K", "RECOVERY_LIMIT", 23, "S\n4\n,F\n104", "S4F104"),
        ("READER-V", "RETRY_COUNT", 61, "S3,\nF28", "S3F28"),
    ):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                         "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
        name = f"{model} guide.pdf"
        db.executemany("INSERT INTO documents VALUES (?,?,?)", [
            (1, "ready", name), (2, "ready", "OTHER guide.pdf"),
            (3, "ready", "Hidden guide.pdf"), (4, "failed", "Failed guide.pdf"),
        ])

        def add(cid, did, content, similarity):
            vec = np.array([similarity, np.sqrt(1 - similarity ** 2)], dtype=np.float32)
            db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, content, vec.tobytes()))

        # A nearby dictionary has the continuous identifier but not the requested
        # field. High-cosine field-only rows must not replace the complete header.
        for i in range(75):
            add(i + 1, 1, f"{field} unrelated{i}", 0.80)
        add(101, 1, f"Message dictionary: {canonical} OTHER_FIELD", 0.90)
        add(102, 1, f"{split}\n{field} recorded={value} " + "detail " * 25, 0.10)
        add(103, 2, f"{canonical} {field} another device", 0.98)
        add(104, 3, f"{split}\n{field} hidden", 0.999)
        add(105, 4, f"{split}\n{field} failed", 0.999)
        add(106, 1, "T\nRANSFER_BLOCKED and RFID-READER TAG", 0.05)
        index = VectorIndex()
        index.reload(db)
        q = np.array([1, 0], dtype=np.float32)

        def token_rows(term):
            token = '"' + term.lower().encode("ascii").hex() + '"'
            return {row[0] for row in index._keywords.execute(
                "SELECT rowid FROM terms WHERE terms MATCH ?", (token,))}

        def search(text, scope={1, 2}, k=3, minimum=0.9999):
            return index.search(q, k, document_ids=scope, min_score=minimum, query_text=text)

        rows = token_rows(canonical)
        check(102 in rows and 104 in rows, f"{model}: split body indexed as complete identifier", sorted(rows))
        check(105 not in rows, f"{model}: failed document omitted from FTS", sorted(rows))
        question = f"《{name}》中 {canonical} 的 {field} 是什么？"
        for k in (1, 3, 60):
            hits = search(question, k=k)
            check([h["chunk_id"] for h in hits] == [102],
                  f"{model}: split header beats same-file dictionary and field-only rows; k={k}", hits)
            check(all(h["document_id"] == 1 and abs(h["score"] - 0.10) < 1e-6 for h in hits),
                  f"{model}: exact recall retains scope and original cosine; k={k}", hits)
        check(search(question, scope={2}) == [], f"{model}: explicit file cannot escape ACL")
        check(search(question, scope=set()) == [], f"{model}: empty ACL")
        hidden = search(f"{canonical} {field}", scope={3}, k=1)
        check(bool(hidden) and hidden[0]["chunk_id"] == 104, f"{model}: authorized split body recall", hidden)
        check(search(f"{canonical} {field}", scope={4}) == [], f"{model}: failed file stays unavailable")
        ordinary = search("", scope={1}, minimum=0.25)
        check(search("普通中文说明", scope={1}, minimum=0.25) == ordinary,
              f"{model}: ordinary semantic ordering unchanged")
        check(search("UNRECORDED_X999", scope={1}, minimum=0.25) == ordinary,
              f"{model}: unknown identifier keeps semantic ordering")
        for term in ("TRANSFER_BLOCKED", "RFID TAG"):
            hits = search(term, scope={1}, k=1)
            check(bool(hits) and hits[0]["chunk_id"] == 106,
                  f"{model}: prior split-event and hyphen aliases remain", hits)

        db.execute("UPDATE documents SET filename='Renamed guide.pdf' WHERE id=1")
        index.reload(db)
        check(search(question) == [], f"{model}: filename reload removes stale identity")
        renamed = search(f"《Renamed guide.pdf》中 {canonical} 的 {field} 是什么？", k=1)
        check(bool(renamed) and renamed[0]["chunk_id"] == 102,
              f"{model}: renamed file retains body alias", renamed)
        db.execute("UPDATE chunks SET content=? WHERE id=102", (f"S8,\nF93 {field} recorded={value}",))
        index.reload(db)
        check(102 not in token_rows(canonical), f"{model}: changed body drops stale alias")
        changed = search(f"《Renamed guide.pdf》中 S8F93 的 {field} 是什么？", k=1)
        check(bool(changed) and changed[0]["chunk_id"] == 102,
              f"{model}: changed body alias appears after reload", changed)
        db.execute("DELETE FROM chunks WHERE id=102")
        index.reload(db)
        check(102 not in token_rows("S8F93"), f"{model}: deleted body drops alias")
        db.execute("UPDATE documents SET status='failed' WHERE id=3")
        index.reload(db)
        check(104 not in token_rows(canonical), f"{model}: failed transition removes body alias")
        db.close()

    # Safety controls are observed in the actual FTS. They do not require a
    # specific helper or regex and allow long split numbers to remain unparsed.
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [
        (1, "ready", "Safety.pdf"), (2, "ready", "S\n9\n,F\n86.pdf"),
    ])
    cases = [
        (1, "S7F141", "s7f141", "s7f14"),
        (2, "S7F142", "s7f142", "s7f14"),
        (3, "S2,F\n4\n1", None, "s2f4"),
        (4, "S2,F\n4\n2", None, "s2f4"),
        (5, "S7\nF14", None, "s7f14"),
        (6, "S7\n|\nF14", None, "s7f14"),
        (7, "S7,\nSECTION\nF14", None, "s7f14"),
        (8, "S7\nHeader\n,F14", None, "s7f14"),
        (9, "XS7,\nF14", None, "s7f14"),
        (10, "S7,\nF14_SUFFIX", None, "s7f14"),
        (11, "S7,\nF14X", None, "s7f14"),
        (12, "S-7,\nF14", None, "s7f14"),
        (13, "S7.2,\nF14", None, "s7f14"),
        (14, "S7,\nF-14", None, "s7f14"),
        (15, "S7,\nF14.5", None, "s7f14"),
        (16, "s7,\nf14", None, "s7f14"),
        (17, "S\n1\n2,F14", None, "s1f14"),
        (18, "S5,\r\nF116", "s5f116", "s5f11"),
        (19, "S9F86", "s9f86", None),
        (21, "S2,F\n4\n\n1", None, "s2f4"),
        (22, "S2,F\n4\f1", None, "s2f4"),
        (23, "S2,F\n4\r\n\r\n1", None, "s2f4"),
    ]
    for cid, content, _, _ in cases:
        vec = np.array([0.1, np.sqrt(0.99)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, 1, content, vec.tobytes()))
    db.execute("INSERT INTO chunks VALUES (?,?,?,?)",
               (20, 2, "NEUTRAL body", np.array([0.1, np.sqrt(0.99)], dtype=np.float32).tobytes()))
    index = VectorIndex()
    index.reload(db)

    def row_has(cid, term):
        token = '"' + term.encode("ascii").hex() + '"'
        return index._keywords.execute(
            "SELECT 1 FROM terms WHERE terms MATCH ? AND rowid=?", (token, cid)).fetchone() is not None

    for cid, content, required, forbidden in cases:
        if required:
            check(row_has(cid, required), f"valid complete body ID survives: {content!r}")
        if forbidden:
            check(not row_has(cid, forbidden), f"no invented short/body ID: {content!r}")
    q = np.array([1, 0], dtype=np.float32)
    check(index.search(q, 1, {1}, 0.9999, "S9,\nF86") == [],
          "body aliases must not normalize split query syntax")
    check(not row_has(20, "s9f86"), "split filename cannot create body alias")
    check(index.search(q, 1, {1, 2}, 0.9999, "S9F86 NEUTRAL") == [],
          "split filename must not acquire a canonical title-only fallback")
    db.close()

    for label, actual in failures:
        print(f"FAIL {label}: {actual}")
    assert not failures, f"{len(failures)}/{checks} checks failed"
    print(f"SECS body alias: PASS ({checks} synthetic checks; no model calls)")


if __name__ == "__main__":
    main()
