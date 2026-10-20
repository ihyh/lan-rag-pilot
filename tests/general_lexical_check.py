"""Generic Chinese/numeric recall is supplemental, scoped and never business-specific."""
import sqlite3
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.index import VectorIndex, lexical_terms


def main():
    numbers = lambda text: {term for term in lexical_terms(text, query=True) if term.startswith("n")}
    assert numbers("11") and numbers("11").isdisjoint(numbers("111 11.5 A11 P11 11_2"))
    assert len(numbers("11.5 -11.5 +11.5 1e3")) == 4
    assert numbers("11.5").isdisjoint(numbers("11 5"))
    assert set(lexical_terms("冷却" , query=True)) <= set(lexical_terms("冷却水温偏高"))
    assert set(lexical_terms("门", query=True)) <= set(lexical_terms("安全门已关闭"))
    assert set(lexical_terms("11", query=True)).isdisjoint(lexical_terms("十一"))
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [(1,"ready","温控.txt"), (2,"ready","隐藏.txt"), (3,"failed","失败.txt")])
    def add(cid, did, text, value):
        vec = np.array([value, np.sqrt(1-value**2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, text, vec.tobytes()))
    for cid in range(1, 121):
        add(cid, 1, "Other value: 111 11.5 A11", .8)
    add(121, 1, "冷却水温度编号 11。安全门已关闭。", .1)
    add(122, 2, "冷却 11 隐藏资料", .99)
    add(123, 3, "冷却 11 下线资料", .99)
    index = VectorIndex()
    index.reload(db)
    vector = np.array([1,0], dtype=np.float32)
    for query in ("11", "冷却", "门"):
        trace = {}
        baseline = index.search(vector, 60, document_ids={1}, min_score=.25, query_text=query, general_lexical=False)
        actual = index.search(vector, 60, document_ids={1}, min_score=.25, query_text=query, general_lexical=True, trace=trace)
        assert 121 not in {hit["chunk_id"] for hit in baseline}
        assert 121 in {hit["chunk_id"] for hit in actual}
        assert trace["channels"]["general"] == [121]
        assert len(actual) <= 60 and all(hit["document_id"] == 1 for hit in actual)
        assert next(hit["score"] for hit in actual if hit["chunk_id"] == 121) < .25
    assert index.search(vector, 60, document_ids=set(), query_text="11", general_lexical=True) == []
    scoped = index.search(vector, 60, document_ids={1,2}, query_text="《温控.txt》的11", general_lexical=True)
    assert scoped and all(hit["document_id"] == 1 for hit in scoped)
    # A numeric match is only a candidate: the final size remains bounded, with no pinned IDs.
    assert len(index.search(vector, 1, document_ids={1}, query_text="11", general_lexical=True)) == 1
    assert 121 not in {hit["chunk_id"] for hit in index.search(vector, 60, document_ids={1}, query_text="112", min_score=.25, general_lexical=True)}
    print("Generic lexical namespaces, Chinese fallback, exact numbers, scope and bounded RRF passed.")


if __name__ == "__main__":
    main()
