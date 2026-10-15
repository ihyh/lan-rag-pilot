"""Explicit filename retrieval binding, without models or private documents."""
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.index import VectorIndex


def main():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY,status TEXT,filename TEXT);"
                     "CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,content TEXT,vector BLOB);")
    names = [(1, "ready", "ALPHA Alarm.txt"), (2, "ready", "BETA 记录.txt"),
             (3, "ready", "BETA 记录.txt"), (4, "failed", "Failed.txt"),
             (5, "ready", "O'Brien 参数.xlsx")]
    db.executemany("INSERT INTO documents VALUES (?,?,?)", names)
    for cid, did in [(i, 1) for i in range(1, 81)] + [(81, 2), (82, 3), (83, 4), (84, 5)]:
        similarity = 0.95 if did == 1 else 0.35
        vector = np.array([similarity, np.sqrt(1 - similarity ** 2)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, "记录值为17，状态未知。", vector.tobytes()))
    index = VectorIndex()
    index.reload(db)
    q = np.array([1, 0], dtype=np.float32)

    def search(text, scope=None, k=3, minimum=0.25):
        return index.search(q, k, document_ids=scope, min_score=minimum, query_text=text)

    ordinary = search("记录值是什么？")
    for text in ("如何把导出文件名设为 `missing.txt`？", "手册示例字符串是 `missing.txt`，扩展名含义？",
                 'OutputFile="BETA 记录.txt"，这是什么字段？', "éBETA 记录.txt 的值？",
                 "如何在配置中将导出文件名设为 missing.txt？", "根据示例把输出文件名设为 missing.txt，该如何配置？",
                 '如何根据 "missing.txt" 这个字符串生成文件路径？'):
        assert search(text) == ordinary, ("file data/partial names are not a source reference", text)
    for text in ("仅依据《BETA 记录.txt》，请不要推断现场现值。", "根据《BETA 记录.txt》回答，不参考其它文件",
                 "《BETA 记录.txt》里的值？不要按《ALPHA Alarm.txt》回答。",
                 "不依据《ALPHA Alarm.txt》回答，只根据《BETA 记录.txt》。",
                 "根据《BETA 记录.txt》不要推断现场现值，只描述日志记录。",
                 "请根据《BETA 记录.txt》说明如何将输出文件名设为 missing.txt。"):
        assert {h["document_id"] for h in search(text)} == {2, 3}, ("unrelated negation must not disable positive binding", text)
    assert search("BETA 记录.txt 中的值？", {1}) == search("MISSING 记录.txt 中的值？", {1}) == [], \
        "invisible and nonexistent bare filenames must be indistinguishable"
    assert search("《ALPHA Alarm.txt》与《BETA 记录.txt》比较", {1}) \
        == search("《ALPHA Alarm.txt》与《MISSING 记录.txt》比较", {1}) == [], \
        "mixed visible/invisible references must not reveal filename existence"
    if len(sys.argv) > 1 and sys.argv[1] == "boundaries":
        print("file reference boundaries: PASS")
        return
    for text in ("仅按 BETA 记录.txt，记录值是什么？", "《BETA 记录.txt》里记录了什么？",
                 '"beta 记录.TXT" 的记录值？', "`BETA 记录.txt` 的记录值？"):
        hits = search(text)
        assert {h["document_id"] for h in hits} == {2, 3}, ("target filename lost before bounded recall", text, hits)
        assert all(abs(h["score"] - 0.35) < 1e-6 for h in hits), "retain original cosine and chunk identity"
    assert {h["document_id"] for h in search("《ALPHA Alarm.txt》与《BETA 记录.txt》比较", k=100)} == {1, 2, 3}
    assert {h["document_id"] for h in search("BETA 记录.txt 中的值？", {2})} == {2}
    for text, scope in (("BETA 记录.txt 中的值？", {1}), ("BETA 记录.txt 中的值？", set()),
                        ("《missing.txt》里的值？", None), ("《Failed.txt》里的值？", None),
                        ("《missing.txt》与《BETA 记录.txt》里的值？", None)):
        assert search(text, scope) == [], ("explicit absent/invisible filename must not substitute another file", text, scope)
    assert search("《BETA 记录.txt》里的值？", k=0) == []
    assert search("《BETA 记录.txt》里的值？", minimum=0.99) == [], "filename identity is not relevance"
    assert {h["document_id"] for h in search("《O'Brien 参数.xlsx》中的值？")} == {5}
    assert search("prefixBETA 记录.txt 的值？") == [], "explicit unknown full name must not match its suffix"
    for text in ("BETA 记录中的值？", "BETA 记录.txt.bak 的值？",
                 "不是《BETA 记录.txt》，而是其它材料", "不要根据 BETA 记录.txt 回答"):
        assert search(text) == ordinary, ("unsupported/negative references preserve existing retrieval", text)
    db.execute("UPDATE documents SET filename='RENAMED.txt' WHERE id IN (2,3)")
    db.execute("UPDATE documents SET status='failed' WHERE id=5")
    index.reload(db)
    assert search("《BETA 记录.txt》里的值？") == [], "reload must discard stale filename cache"
    assert search("《O'Brien 参数.xlsx》里的值？") == [], "failed documents are not filename targets"
    assert {h["document_id"] for h in search("《RENAMED.txt》里的值？")} == {2, 3}
    for cid, did in ((85, 1), (86, 2)):
        vector = np.array([0.1, np.sqrt(0.99)], dtype=np.float32)
        db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (cid, did, "TXID 的值为29。", vector.tobytes()))
    index.reload(db)
    assert [h["chunk_id"] for h in search("《RENAMED.txt》中 TXID 的值？", minimum=0.99)] == [86], \
        "exact FTS recall must share filename/ACL scope even below semantic threshold"
    assert search("《RENAMED.txt》中 TXID 的值？", {1}, minimum=0.99) == []
    db.execute("DELETE FROM chunks")
    index.reload(db)
    assert search("《RENAMED.txt》里的值？") == [], "empty reload clears file identities"
    db.close()
    print("filename scope: PASS (starvation, duplicates, comparison, ACL, missing/failed, boundaries, reload)")


if __name__ == "__main__":
    main()
