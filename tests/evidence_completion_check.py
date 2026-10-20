"""字面标识符补证检查：问题点名代码/字段时跨文档补齐原文，且不改范围/引用语义。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evidence_completion import (  # noqa: E402
    MAX_EXTRA_SOURCES,
    attach_literal_evidence,
    question_literals,
)

PASS = 0
FAIL = 0


def check(condition: bool, message: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {message}")
    else:
        FAIL += 1
        print(f"  [FAIL] {message}")


def make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT NOT NULL, status TEXT NOT NULL);"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,"
        " seq INTEGER NOT NULL, page INTEGER, paragraph INTEGER, content TEXT);"
    )
    return db


def add_document(db: sqlite3.Connection, document_id: int, filename: str,
                 status: str = "ready") -> None:
    db.execute("INSERT INTO documents(id, filename, status) VALUES(?,?,?)",
               (document_id, filename, status))


def add_chunk(db: sqlite3.Connection, chunk_id: int, document_id: int, seq: int,
              content: str, page: int | None = None) -> None:
    db.execute(
        "INSERT INTO chunks(id, document_id, seq, page, paragraph, content) VALUES(?,?,?,?,?,?)",
        (chunk_id, document_id, seq, page, 1, content),
    )


def source(chunk_id: int, document_id: int, seq: int, filename: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id, "document_id": document_id, "seq": seq,
        "filename": filename, "page": None, "paragraph": 1,
        "score": 0.5, "content": content,
    }


def main() -> int:
    print("\n== 字面标识符提取 ==")
    check(question_literals("完整代码2,1分别怎么解释？") == ["2,1"], "中文紧邻的告警代码对可提取")
    tokens = question_literals("PLM200W参数说明的P150，bit4分别记录什么？")
    check("P150" in tokens and "bit4" in tokens, "P150/bit4 可提取")
    check("Input Monitor Time" in question_literals("Input Monitor Time的索引和单位是什么？"),
          "多词英文字段名可提取（不要求尾部单词边界）")
    check(question_literals("几是正常片，几是斜片") == [], "普通问句不触发字面补证")
    check("S2F20" in question_literals("S2F20 <U1[1] 0>是什么意思"), "SxFy 可提取")

    print("\n== 跨文档补齐同号不同定义 ==")
    db = make_db()
    add_document(db, 33, "Plus500_Alarm.txt")
    add_document(db, 34, "PlusPro_Alarm_pro.txt")
    add_chunk(db, 1269, 33, 0, "2,1,ROBOT_NO_HEX_CHANNEL_ERROR")
    add_chunk(db, 1272, 34, 0, "2,1,Unknown Command")
    result = attach_literal_evidence(
        db, [], "Plus500_Alarm.txt 与 PlusPro_Alarm_pro.txt 中的完整代码2,1分别怎么解释？",
        document_ids={33, 34},
    )
    check(len(result) == 2 and {item["document_id"] for item in result} == {33, 34},
          "同号代码在两份文档的原文都补入")
    check(all(item.get("matched_literal") == "2,1" for item in result), "来源记录命中的字面")
    check(all(item["score"] is None for item in result), "补证来源不伪造相似度")
    check(all(item.get("relation") == "question_literal" for item in result), "补证来源标记关系类型")

    print("\n== 会话范围与去重 ==")
    result = attach_literal_evidence(db, [], "代码2,1怎么解释", document_ids={33})
    check(len(result) == 1 and result[0]["document_id"] == 33, "只补当前范围文档")
    existing = [source(1269, 33, 0, "Plus500_Alarm.txt", "2,1,ROBOT_NO_HEX_CHANNEL_ERROR")]
    result = attach_literal_evidence(db, existing, "代码2,1怎么解释", document_ids={33, 34})
    check(len(result) == 2 and result[1]["chunk_id"] == 1272, "已在来源中的片段不重复附加")
    result = attach_literal_evidence(db, [], "代码2,1怎么解释")
    check(len(result) == 2, "不传范围时按全库补证")

    print("\n== xlsx 目标行 + 表头 ==")
    db = make_db()
    add_document(db, 26, "PLM_parameters.xlsx")
    add_chunk(db, 1155, 26, 0,
              "工作表《Parameter》第 1 行：A1=Index；B1=Name；C1=Unit；D1=Description；E1=Mark")
    add_chunk(db, 1157, 26, 2,
              "工作表《Parameter》第 18 行：A18=20；B18=Input Monitor Time；C18=/；"
              "D18=监控输入的时间间隔；E18=扫描输入变化的时间间隔,单位ms")
    result = attach_literal_evidence(
        db, [], "Input Monitor Time 的索引、字段说明及时间单位是什么？", document_ids={26},
    )
    chunk_ids = [item["chunk_id"] for item in result]
    check(1157 in chunk_ids, "目标参数行被补入")
    check(1155 in chunk_ids, "xlsx 表头段被补入")
    header = next(item for item in result if item["chunk_id"] == 1155)
    check(header["relation"] == "table_header", "表头来源标记 table_header")

    print("\n== 无匹配 / 停用词 / 数量上限 ==")
    result = attach_literal_evidence(db, [], "P20 的现场当前值是多少", document_ids={26})
    check(result == [], "字面在范围内不存在时不补")
    result = attach_literal_evidence(db, [], "E84 自动模式", document_ids={26})
    check(result == [], "停用词（E84）不触发字面补证")
    db2 = make_db()
    for index in range(6):
        add_document(db2, 100 + index, f"doc{index}.txt")
        add_chunk(db2, 300 + index, 100 + index, 0, "2,1,重复代码")
    result = attach_literal_evidence(db2, [], "代码2,1怎么解释", document_ids=set(range(100, 106)))
    check(len(result) <= MAX_EXTRA_SOURCES, f"补证总数有上限 {MAX_EXTRA_SOURCES}")
    check(len({item["document_id"] for item in result}) == len(result), "同一标识符优先覆盖不同文档")

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
