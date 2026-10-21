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

    print("\n== 标识符边界与 LIKE 元字符 ==")
    db = make_db()
    add_document(db, 1, "alarm.txt")
    add_document(db, 2, "alarm2.txt")
    add_chunk(db, 400, 1, 0, "12,10,OTHER_CODE")
    add_chunk(db, 401, 2, 0, "2,1,REAL_CODE")
    result = attach_literal_evidence(db, [], "代码2,1怎么解释", document_ids={1, 2})
    check([item["chunk_id"] for item in result] == [401],
          "2,1 不匹配 12,10（代码前后数字边界）")
    check(all("12,10" not in (item["content"] or "") for item in result), "无关代码不进入补证")
    db = make_db()
    add_document(db, 1, "fields.txt")
    add_chunk(db, 402, 1, 0, "bit40 = 另一个字段")
    add_chunk(db, 403, 1, 1, "bit4 = E84触发事件")
    result = attach_literal_evidence(db, [], "bit4 是什么意思", document_ids={1})
    check([item["chunk_id"] for item in result] == [403], "bit4 不匹配 bit40（字段后缀边界）")
    db = make_db()
    add_document(db, 1, "tokens.txt")
    add_chunk(db, 404, 1, 0, "AXB 只是相似字符串")
    add_chunk(db, 405, 1, 1, "A_B 是真正的字段")
    result = attach_literal_evidence(db, [], '字段“A_B”是什么', document_ids={1})
    check([item["chunk_id"] for item in result] == [405],
          "引号内 A_B 不匹配 AXB（LIKE 下划线已转义）")

    print("\n== 早期大文档重复片段不挤掉后导入文档 ==")
    db = make_db()
    add_document(db, 1, "big.txt")
    for index in range(50):
        add_chunk(db, 500 + index, 1, index, f"2,1,文档A的第{index}条重复说明")
    add_document(db, 2, "later.txt")
    add_chunk(db, 700, 2, 0, "2,1,文档B的同号另一解释")
    result = attach_literal_evidence(db, [], "代码2,1分别怎么解释", document_ids={1, 2})
    documents = {item["document_id"] for item in result}
    check(2 in documents, "后导入文档的同号定义进入候选（不按前40条截断）")
    check({700} <= {item["chunk_id"] for item in result}, "后导入文档的具体片段被补入")
    check(len(result) <= MAX_EXTRA_SOURCES, "补证总数仍受上限约束")

    print("\n== 精确过滤之前的伪匹配不能挤掉真匹配 ==")
    db = make_db()
    add_document(db, 1, "fields.txt")
    for index in range(4):
        add_chunk(db, 900 + index, 1, index, f"bit40 = 伪字段 {index}")
    add_chunk(db, 904, 1, 4, "bit4 = 真正目标字段")
    result = attach_literal_evidence(db, [], "bit4 是什么", document_ids={1})
    check([item["chunk_id"] for item in result] == [904],
          "前四条伪匹配被过滤后，仍继续读到真正的 bit4")
    db = make_db()
    add_document(db, 1, "alarm.txt")
    for index in range(4):
        add_chunk(db, 910 + index, 1, index, f"12,10 = 伪代码 {index}")
    add_chunk(db, 914, 1, 4, "2,1 = 真正代码")
    result = attach_literal_evidence(db, [], "代码2,1是什么", document_ids={1})
    check([item["chunk_id"] for item in result] == [914],
          "代码对的伪匹配同样不占用真匹配名额")

    print("\n== 同表标题行不被当成表头 ==")
    db = make_db()
    add_document(db, 26, "PLM_parameters.xlsx")
    add_chunk(db, 820, 26, 0, "工作表《Parameter》第 1 行：A1=参数表")
    add_chunk(db, 821, 26, 1,
              "工作表《Parameter》第 2 行：A2=Index；B2=Name；C2=Unit；D2=Description")
    add_chunk(db, 822, 26, 2,
              "工作表《Parameter》第 3 行：A3=20；B3=Input Monitor Time；C3=/；"
              "D3=监控输入的时间间隔")
    result = attach_literal_evidence(
        db, [], "Input Monitor Time 的单位和索引是什么？", document_ids={26},
    )
    chunk_ids = {item["chunk_id"] for item in result}
    check(822 in chunk_ids, "目标参数行被补入")
    check(821 in chunk_ids, "第 2 行的真实列名被识别为表头")
    check(820 not in chunk_ids, "同表第 1 行标题不被标记为表头")
    header = next(item for item in result if item["chunk_id"] == 821)
    check(header["relation"] == "table_header", "真实列名行标记 table_header")
    db2 = make_db()
    add_document(db2, 27, "titleonly.xlsx")
    add_chunk(db2, 823, 27, 0, "工作表《SheetX》第 1 行：A1=标题")
    add_chunk(db2, 824, 27, 1, "工作表《SheetX》第 4 行：A4=20；B4=Input Monitor Time")
    result = attach_literal_evidence(
        db2, [], "Input Monitor Time 的索引是什么？", document_ids={27},
    )
    check(all(item["relation"] != "table_header" for item in result),
          "只有标题行时不得标记 table_header")

    print("\n== 多工作表：按目标工作表绑定表头 ==")
    db = make_db()
    add_document(db, 26, "PLM_parameters.xlsx")
    add_chunk(db, 800, 26, 0, "工作表《Cover》第 1 行：A1=封面标题；B1=版本")
    add_chunk(db, 801, 26, 1, "工作表《Cover》第 2 行：A2=编制单位")
    add_chunk(db, 802, 26, 2,
              "工作表《Parameter》第 1 行：A1=Index；B1=Name；C1=Unit；D1=Description；E1=Mark")
    add_chunk(db, 803, 26, 3,
              "工作表《Parameter》第 18 行：A18=20；B18=Input Monitor Time；C18=/；"
              "D18=监控输入的时间间隔；E18=扫描输入变化的时间间隔,单位ms")
    result = attach_literal_evidence(
        db, [], "Input Monitor Time 的索引和单位是什么？", document_ids={26},
    )
    chunk_ids = {item["chunk_id"] for item in result}
    check(803 in chunk_ids, "目标参数行被补入")
    check(802 in chunk_ids, "按目标工作表补入 Parameter 真实表头")
    check(800 not in chunk_ids, "不拿封面首段冒充参数表头")
    header = next(item for item in result if item["chunk_id"] == 802)
    check(header["relation"] == "table_header", "表头来源标记 table_header")
    db2 = make_db()
    add_document(db2, 27, "other.xlsx")
    add_chunk(db2, 810, 27, 0, "工作表《Cover》第 1 行：A1=封面")
    add_chunk(db2, 811, 27, 5,
              "工作表《MissingSheet》第 7 行：A7=Input Monitor Time；B7=值")
    result = attach_literal_evidence(
        db2, [], "Input Monitor Time 是什么？", document_ids={27},
    )
    check(811 in {item["chunk_id"] for item in result}, "目标行被补入")
    check(all(item["relation"] != "table_header" for item in result),
          "无法可靠定位表头时不标记 table_header")

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
