"""Current/auxiliary semantics use 2:1 RRF and exactly the current-query scope."""
import sqlite3
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.index import VectorIndex, semantic_queries


def main():
    history = [{"question": "上一轮参数", "answer": "NEVER_RETRIEVE_THIS_ANSWER"}]
    assert semantic_queries("当前问题", history, True) == ("当前问题", "上一轮参数\n当前问题")
    assert semantic_queries("当前问题", history, False) == ("上一轮参数\n当前问题", None)
    assert semantic_queries("当前问题", [], True) == ("当前问题", None)
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [(1,"ready","当前.txt"), (2,"ready","旧话题.txt")])
    def add(cid, did, vector):
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, f"value {cid}", np.array(vector, dtype=np.float32).tobytes()))
    add(1,1,[1,0])
    add(2,1,[0,1])
    add(3,2,[.7,.7])
    index = VectorIndex()
    index.reload(db)
    current, auxiliary = np.array([1,0], dtype=np.float32), np.array([0,1], dtype=np.float32)
    trace = {}
    hits = index.search(current, 2, document_ids={1}, query_text="11", auxiliary_vec=auxiliary, trace=trace)
    assert [h["chunk_id"] for h in hits] == [1,2]
    assert trace["semantic_weights"] == [2,1]
    assert trace["channels"]["semantic_current"] == [1,2]
    assert trace["channels"]["semantic_history"] == [2,1]
    assert hits[1]["score"] == 0, "historical rank score replaced original current-query cosine"
    scoped = index.search(current, 3, document_ids={1,2}, query_text="《当前.txt》", auxiliary_vec=auxiliary)
    assert scoped and all(h["document_id"] == 1 for h in scoped)
    # An auxiliary match can supply a candidate if the current cosine misses its threshold.
    high = index.search(np.array([-1,0]), 2, document_ids={1}, min_score=.8, auxiliary_vec=auxiliary)
    assert [h["chunk_id"] for h in high] == [2]
    assert high[0]["score"] == 0
    assert index.search(current, 3, document_ids=set(), auxiliary_vec=auxiliary) == []
    try:
        index.search(current, 2, auxiliary_vec=np.array([1,0,0]))
    except ValueError:
        pass
    else:
        raise AssertionError("mismatched auxiliary dimensions accepted")
    print("Current-first 2:1 semantic fusion, history-answer exclusion and common scope passed.")


if __name__ == "__main__":
    main()
