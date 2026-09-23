"""Adversarial synthetic recall checks: no model or business data required."""
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.index import VectorIndex


def main():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?)", [(1, "ready"), (2, "ready"), (3, "failed")])
    def add(cid, did, text, similarity):
        vec = np.array([similarity, np.sqrt(1-similarity**2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, text, vec.tobytes()))
    for cid in range(1, 121):
        add(cid, 1, "ArmElev的P200是别的参数。", 0.8)
    add(121, 1, "ArmElev的P20在参数页修改，确认后保存。", 0.1)
    add(122, 2, "ArmElev P20", 0.9)
    add(123, 3, "ArmElev P20", 0.99)
    add(124, 1, "只写P20，没有轴名。", 0.7)
    add(125, 1, "RESET_AXIS命令用于复位。", 0.05)
    add(126, 1, "RESET_ALL不是指定命令。", 0.85)
    index = VectorIndex()
    index.reload(db)
    q = np.array([1, 0], dtype=np.float32)
    baseline = index.search(q, 5, {1}, min_score=0.25)
    assert 121 not in [h["chunk_id"] for h in baseline]
    hybrid = index.search(q, 5, {1}, min_score=0.25, query_text="如何修改armelev的p20？")
    assert hybrid[0]["chunk_id"] == 121, hybrid
    assert abs(hybrid[0]["score"] - 0.1) < 1e-6, "keep cosine score, not fusion score"
    assert all(h["document_id"] == 1 for h in hybrid)
    assert index.search(q, 5, set(), query_text="ArmElev P20") == []
    assert index.search(q, 5, {3}, query_text="ArmElev P20") == []
    assert index.search(q, 5, {1}, 0.25, query_text="如何设置参数？") == baseline
    assert index.search(q, 5, {1}, 0.25, query_text='不存在X999 " OR *') == baseline
    assert index.search(q, 5, {1}, 0.25, query_text="RESET_AXIS命令怎么用")[0]["chunk_id"] == 125
    assert index.search(q, 0, {1}, query_text="P20") == []
    lexical_only = index.search(q, 1, {1}, min_score=0.999, query_text="ArmElev P20")
    assert len(lexical_only) == 1 and lexical_only[0]["chunk_id"] == 121
    # 一个"哪个切片里都没有"的标识符，绝不能把整个精确匹配通道打成空。
    # 问题里出现文档中不存在的写法是常态——产品名写在文件名里、正文写的是别的型号——
    # 而旧的 AND 语义（所有术语全要命中）下，这种问题会让精确匹配**静默**失效，
    # 只剩向量检索，于是答成另一台设备的内容。真实语料上就发生过：
    # 「Fortrend LP PxM 软件里，如何设置设备的通讯参数？」提取出 fortrend/lp/pxm，
    # 而手册正文写的是 LP/PLM、LP-150，从没有 PxM，于是 AND 命中 0 个切片。
    dead_term = index.search(q, 5, {1}, 0.25, query_text="ArmElev P20 ZZTOP")
    assert dead_term[0]["chunk_id"] == 121, f"含不存在术语时精确匹配仍须生效：{dead_term}"
    # 全部术语都不存在时：退回纯语义检索，既不报错也不凭空命中。
    assert index.search(q, 5, {1}, 0.25, query_text="ZZTOP") == baseline
    # 剔除的只能是"零倒排项"的术语：其余术语之间的 AND 精度必须保持不变，
    # 问 P200 不能因为 P20 存在就把 P20 的切片捞出来。
    lexical_p200 = index.search(q, 1, {1}, min_score=0.999, query_text="ArmElev P200")
    assert lexical_p200 and lexical_p200[0]["chunk_id"] != 121, lexical_p200
    # Check query/reload coordination across the web server's worker threads.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(index.search, q, 5, {1}, 0.25, "ArmElev P20") for _ in range(12)]
        index.reload(db)
        assert all(f.result()[0]["chunk_id"] == 121 for f in futures)
    # Upload/reindex/delete/status changes all use reload; stale keyword rows must disappear.
    db.execute("DELETE FROM chunks WHERE id=121")
    index.reload(db)
    assert all(h["chunk_id"] != 121 for h in index.search(q, 5, {1}, query_text="ArmElev P20"))
    db.execute("UPDATE documents SET status='failed'")
    index.reload(db)
    assert index.search(q, 5, query_text="ArmElev P20") == []
    db.close()
    print("Hybrid retrieval PASS: recall beyond vector pool, exact identifiers, scope, lifecycle, semantic fallback")


if __name__ == "__main__":
    main()
