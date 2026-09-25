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
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [
        (1, "ready", "ArmElev 手册"), (2, "ready", "其它设备"), (3, "failed", "已下线设备")])
    def add(cid, did, text, similarity):
        vec = np.array([similarity, np.sqrt(1-similarity**2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, text, vec.tobytes()))
    for cid in range(1, 121):
        add(cid, 1, "ArmElev的P200是别的参数。", 0.8)
    add(121, 1, "ArmElev的P20在参数页修改，确认后保存。", 0.1)
    add(122, 2, "ArmElev P20 DOC2ONLY", 0.9)
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
    scoped_term = index.search(q, 1, {1}, min_score=0.999, query_text="ArmElev P20 DOC2ONLY")
    assert scoped_term and scoped_term[0]["chunk_id"] == 121, f"范围外术语不得清空精确匹配：{scoped_term}"
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

    # 型号只在文件名与另一片正文中；目标片仍须按其余术语精确召回。
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [
        (1, "ready", "PLUS-500 测试.xlsx"), (2, "ready", "其它设备.xlsx"),
        (3, "ready", "PLUS-500 不可见资料.xlsx")])
    def add_case(cid, did, text, similarity):
        vec = np.array([similarity, np.sqrt(1-similarity**2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, text, vec.tobytes()))
    add_case(1, 1, "PLUS-500 测试封面", 0.98)
    add_case(2, 1, "SECS E84 AUTO MODE SET_ACCESS_MODE_AUTO", 0.10)
    add_case(3, 1, "SECS E84 REMOTE MODE SET_ACCESS_MODE_MANUAL", 0.95)
    add_case(4, 2, "SECS E84 AUTO MODE 属于另一台设备", 0.99)
    add_case(5, 2, "无关说明", 0.96)
    add_case(6, 3, "PLUS-500 SECS E84 AUTO MODE 不可见内容", 0.999)
    index.reload(db)
    question = "PLUS-500 的 SECS 测试里，在 E84 AUTO MODE 下发送什么指令？"
    hits_by_limit = {}
    for result_limit in (1, 2, 3, 8):
        try:
            hits_by_limit[result_limit] = index.search(q, result_limit, {1, 2}, 0.25, question)
        except KeyError as exc:
            raise AssertionError("文件名回退不得让不可见文档进入关键词候选") from exc
        assert all(hit["document_id"] in {1, 2} for hit in hits_by_limit[result_limit]), \
            "文件名回退不得让不可见文档进入关键词候选"
    hits = hits_by_limit[3]
    assert hits[0]["chunk_id"] == 2, f"文件名限定文档后应召回 AUTO 指令：{hits}"
    assert 3 not in [hit["chunk_id"] for hit in hits], "REMOTE 指令不得挤入 AUTO 引用集"
    assert 6 not in [hit["chunk_id"] for hit in hits], \
        "文件名回退不得把不可见文档的高分切片带进引用集"
    stale_tokens = " ".join(term.encode("ascii").hex()
                            for term in ("plus-500", "secs", "e84", "auto", "mode"))
    index._keywords.execute(
        "INSERT INTO terms(rowid,document_id,tokens) VALUES (?,?,?)", (7, 1, stale_tokens)
    )
    try:
        stale_safe = index.search(q, 8, {1, 2}, 0.25, question)
    except KeyError as exc:
        raise AssertionError("范围外或陈旧关键词候选不得导致检索崩溃") from exc
    assert 7 not in [hit["chunk_id"] for hit in stale_safe], \
        "范围外或陈旧关键词候选不得进入检索结果"
    assert all(hit["document_id"] == 2 for hit in index.search(q, 3, {2}, 0.25, question)), \
        "文件名回退不得跨越可见文档范围"
    db.execute("UPDATE documents SET filename='已更名的测试文件.xlsx' WHERE id=1")
    index.reload(db)
    assert 2 not in [hit["chunk_id"] for hit in index.search(q, 3, {1, 2}, 0.25, question)], \
        "文件名变更并重载后不得使用旧标题术语"
    db.close()

    # 只有一个宽泛技术词、且多个可见文件名都包含它时，不应让封面正文压过语义结果。
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [
        (1, "ready", "FAMILY-X 测试.xlsx"), (2, "ready", "FAMILY-X 手册.pdf")])
    for cid, did, content, similarity in [
        (20, 1, "状态回复参数解析见本节。", 0.90),
        (21, 2, "FAMILY-X 封面", 0.10),
    ]:
        vec = np.array([similarity, np.sqrt(1-similarity**2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, content, vec.tobytes()))
    index.reload(db)
    broad = index.search(q, 1, {1, 2}, query_text="FAMILY-X 的状态回复参数解析是什么？")
    assert broad[0]["chunk_id"] == 20, f"多文件共享的单一型号词不得压过语义结果：{broad}"
    db.close()

    # 标题独有的型号被 live_terms 剔除时，别的文档正文命中也不能盖过目标文档。
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    db.executemany("INSERT INTO documents VALUES (?,?,?)", [
        (1, "ready", "MODEL-A.pdf"), (2, "ready", "OTHER.pdf")])
    for cid, did, content, similarity in [
        (10, 1, "E84 AUTO MODE correct " + "noise " * 50, 0.10),
        (11, 2, "E84 AUTO MODE wrong", 0.90),
    ]:
        vec = np.array([similarity, np.sqrt(1-similarity**2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, content, vec.tobytes()))
    index.reload(db)
    assert index._keyword_exists("model-a", {1, 2}) is False
    assert index.search(q, 1, {1, 2}, query_text="MODEL-A E84 AUTO MODE")[0]["chunk_id"] == 10, \
        "标题独有型号应限定正文术语检索的文档"
    assert all(hit["document_id"] == 2 for hit in
               index.search(q, 1, {2}, query_text="MODEL-A E84 AUTO MODE")), \
        "标题独有型号不得突破可见文档范围"
    db.close()
    print("Hybrid retrieval PASS: recall beyond vector pool, exact identifiers, scope, lifecycle, semantic fallback")


if __name__ == "__main__":
    main()
