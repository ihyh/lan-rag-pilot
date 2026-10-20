"""续段上下文组装检查：只补同文档下一段，条件来自原文结构。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.context_assembly import MAX_EXTRA_SOURCES, attach_continuations  # noqa: E402

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
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, filename TEXT NOT NULL);"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL,"
        " seq INTEGER NOT NULL, page INTEGER, paragraph INTEGER, content TEXT);"
    )
    return db


def add_document(db: sqlite3.Connection, document_id: int, filename: str) -> None:
    db.execute("INSERT INTO documents(id, filename) VALUES(?,?)", (document_id, filename))


def add_chunk(db: sqlite3.Connection, chunk_id: int, document_id: int, seq: int,
              page: int | None, content: str) -> None:
    db.execute(
        "INSERT INTO chunks(id, document_id, seq, page, paragraph, content) VALUES(?,?,?,?,?,?)",
        (chunk_id, document_id, seq, page, 1, content),
    )


def source(chunk_id: int, document_id: int, seq: int, page: int | None,
           filename: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "seq": seq,
        "page": page,
        "paragraph": 1,
        "filename": filename,
        "score": 0.5,
        "content": content,
    }


def main() -> int:
    print("\n== 续段补齐：未完结的尾句 ==")
    db = make_db()
    add_document(db, 1, "manual.pdf")
    add_chunk(db, 10, 1, 0, 6, "Parameters: Byte 1: Inter-Character time-out (must be >4: if not, default")
    add_chunk(db, 11, 1, 1, 7, "Byte 2: ENQ-EOT time-out (must be >19).\nByte 3: retry count (must be >1).")
    sources = [source(10, 1, 0, 6, "manual.pdf", db.execute("SELECT content FROM chunks WHERE id=10").fetchone()[0])]
    result = attach_continuations(db, sources)
    check(len(result) == 2 and result[1]["chunk_id"] == 11, "未完结尾句后附加同文档下一段")
    check(result[1]["continuation_of"] == 10 and result[1]["page"] == 7, "续段保留锚点、真实页码")
    check(result[1]["score"] is None, "续段不伪造相似度分数")
    check(result[0] is sources[0], "原选段对象与顺序不变，续段紧随其后")

    print("\n== 续段补齐：完整句 + 页眉噪声后的 Byte 续写 ==")
    db = make_db()
    add_document(db, 1, "rfid.pdf")
    anchor_text = ("Configure RFT-200S RFID READER: ID 0xA5\n"
                   "Parameters: Byte 1: Inter-Character time-out in tenths of seconds (must be >4: if not, reverts to default\n"
                   " value of 40).\n"
                   "\x00F\x00o\x00r\x00t\x00r\x00e\x00n\x00d\x00 \x00\n\x00C\x00o\x00n\x00f\x00i\x00d\x00e\x00n\x00t\x00i\x00a\x00l\x00\n\x00N\x00o\x00 \x00C\x00o\x00p\x00y")
    following_text = ("Transfer Controller Interface 7 5/23/2017\n"
                      " Byte 2: ENQ-EOT time-out in tenths of seconds (must be >19: if not, reverts to default\n"
                      " value of 200).\n"
                      " Byte 3: Message retry count (must be >1: if no, reverts to default value of 2).")
    add_chunk(db, 20, 1, 0, 6, anchor_text)
    add_chunk(db, 21, 1, 1, 7, following_text)
    result = attach_continuations(db, [source(20, 1, 0, 6, "rfid.pdf", anchor_text)])
    check(len(result) == 2 and result[1]["chunk_id"] == 21, "页眉噪声后的 Byte 续写仍被识别")

    print("\n== 不应扩段：括注行结尾 + 下一页是新章节 ==")
    db = make_db()
    add_document(db, 1, "pxm.pdf")
    add_chunk(db, 35, 1, 0, 11,
              "mapStat：最终的判断结果，有 PECD四种结果，分别代表正常片，空片，斜片，厚片。\n"
              "[用于 PLUS和设备的教导器开发]")
    add_chunk(db, 36, 1, 1, 12,
              "LP-150软件操作手册\nRev:11\n1.2.5 Tool 工具\nTool工具栏包含调试或者系统的工具，例如 Macro的指令调用。")
    result = attach_continuations(db, [source(35, 1, 0, 11, "pxm.pdf",
                                             "mapStat：最终的判断结果，有 PECD四种结果。\n"
                                             "[用于 PLUS和设备的教导器开发]")])
    check(len(result) == 1, "括注行结尾不视为未完结；下一页新章节不扩段")

    print("\n== 不应扩段：完整句 + 无关下一页 ==")
    db = make_db()
    add_document(db, 1, "manual.pdf")
    add_chunk(db, 30, 1, 0, 1, "5.2 卸载移载\n本节说明卸载移载的时序。")
    add_chunk(db, 31, 1, 1, 2, "图5.2 Timing Chart\n信号方向说明。")
    result = attach_continuations(db, [source(30, 1, 0, 1, "manual.pdf",
                                             "5.2 卸载移载\n本节说明卸载移载的时序。")])
    check(len(result) == 1, "完整句且下一页无续写标记时不扩段")

    print("\n== 不应扩段：xlsx 行 / 无 seq / 空内容 ==")
    db = make_db()
    add_document(db, 1, "params.xlsx")
    add_chunk(db, 40, 1, 0, None, "工作表《Parameter》第 1 行：A1=Index；B1=Name")
    add_chunk(db, 41, 1, 1, None, "工作表《Parameter》第 2 行：A2=0；B2=Input Monitor1")
    result = attach_continuations(db, [source(40, 1, 0, None, "params.xlsx", "A1=Index")])
    check(len(result) == 1, "xlsx 不扩段（行语义靠表头）")
    result = attach_continuations(db, [dict(source(40, 1, 0, None, "params.xlsx", "A1=Index"), seq=None)])
    check(len(result) == 1, "缺少 seq 时安全跳过")
    db2 = make_db()
    add_document(db2, 1, "empty.pdf")
    add_chunk(db2, 50, 1, 0, 1, "anchor without end")
    add_chunk(db2, 51, 1, 1, 2, "   \n  ")
    result = attach_continuations(db2, [source(50, 1, 0, 1, "empty.pdf", "anchor without end")])
    check(len(result) == 1, "下一段为空时不附加")

    print("\n== 数量上限与去重 ==")
    db = make_db()
    add_document(db, 1, "many.pdf")
    for index in range(4):
        add_chunk(db, 60 + index, 1, index, index + 1, f"步骤 {index + 1}：继续")
    anchors = [source(60, 1, 0, 1, "many.pdf", "步骤 1：继续"),
               source(62, 1, 2, 3, "many.pdf", "步骤 3：继续")]
    result = attach_continuations(db, anchors)
    check(len(result) == len(anchors) + MAX_EXTRA_SOURCES, f"最多附加上限 {MAX_EXTRA_SOURCES} 段")
    check(len({item["chunk_id"] for item in result}) == len(result), "续段不与已有来源重复")
    # 相邻锚点的下一段已被另一个锚点占用时，不重复附加
    adjacent = [source(60, 1, 0, 1, "many.pdf", "步骤 1：继续"),
                source(61, 1, 1, 2, "many.pdf", "步骤 2：继续")]
    result = attach_continuations(db, adjacent)
    check(len(result) == 3 and result[2]["chunk_id"] == 62, "已被选中的下一段不重复附加")
    result = attach_continuations(db, anchors, max_extra=0)
    check(len(result) == len(anchors), "max_extra=0 时不附加")

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
