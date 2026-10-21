"""补证来源的持久化契约检查：普通/流式/历史来源、失败回滚、旧库升级。"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TMP = Path(tempfile.mkdtemp(prefix="rag-persist-"))
os.environ.update({
    "RAG_DATA_DIR": str(TMP),
    "RAG_DB_PATH": str(TMP / "persist.db"),
    "RAG_UPLOAD_DIR": str(TMP / "uploads"),
    "RAG_MODELS_DIR": str(TMP / "models"),
    "RAG_EMBED_BACKEND": "mock",
    "RAG_SECRET_KEY": "persist-check-secret-0123456789",
    "RAG_ROOT_PASSWORD": "persist-check-root-pw",
    "RAG_QUERIES_PER_MINUTE": "100",
})

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db as db_module  # noqa: E402
from app.routers import query as route  # noqa: E402
from app.schemas import QueryBody  # noqa: E402

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


def add_document(database: sqlite3.Connection, document_id: int, filename: str) -> None:
    database.execute(
        "INSERT INTO documents(id, filename, stored_name, content_type, size_bytes, sha256,"
        " status, num_chunks, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?,'ready',0,'2026-10-21T00:00:00+00:00','2026-10-21T00:00:00+00:00')",
        (document_id, filename, f"stored-{document_id}", "text/plain", 10, f"sha-{document_id}"),
    )


def add_chunk(database: sqlite3.Connection, chunk_id: int, document_id: int, seq: int,
              content: str, page: int | None = None) -> None:
    database.execute(
        "INSERT INTO chunks(id, document_id, seq, page, paragraph, token_count, content, vector)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (chunk_id, document_id, seq, page, 1, 10, content, b""),
    )


def setup_db() -> sqlite3.Connection:
    db_module.init_db()
    database = db_module.connect()
    database.execute("PRAGMA foreign_keys = OFF")
    for table in ("chat_sources", "chats", "conversations", "chunks", "documents", "users",
                  "audit_logs", "feedback", "sessions"):
        try:
            database.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    database.execute("PRAGMA foreign_keys = ON")
    database.execute(
        "INSERT INTO users(id, username, password_hash, role, created_at, updated_at)"
        " VALUES(1,'persist-tester','x','root','2026-10-21T00:00:00+00:00','2026-10-21T00:00:00+00:00')"
    )
    add_document(database, 1, "manual.pdf")
    add_chunk(database, 10, 1, 0, "Parameters: Byte 1: timeout (must be >4: default", page=1)
    add_chunk(database, 11, 1, 1, "Byte 2: ENQ-EOT timeout (must be >19: default 200).", page=2)
    add_document(database, 2, "params.xlsx")
    add_chunk(database, 20, 2, 0,
              "工作表《Parameter》第 1 行：A1=Index；B1=Name；C1=Unit；D1=Description")
    add_chunk(database, 21, 2, 1,
              "工作表《Parameter》第 18 行：A18=20；B18=Input Monitor Time；"
              "D18=监控输入的时间间隔；E18=单位ms")
    add_document(database, 3, "other.txt")
    add_chunk(database, 30, 3, 0, "Input Monitor Time：另一份文档里的同名字段定义")
    database.commit()
    return database


def fake_index(chunk_ids: list[int]) -> MagicMock:
    index = MagicMock()
    index.size.return_value = len(chunk_ids)
    index.search.return_value = [{"chunk_id": chunk_id, "score": 0.5} for chunk_id in chunk_ids]
    index.table_headers_for.return_value = {}
    return index


def patches(index: MagicMock, switches: dict[str, bool]) -> list:
    stream_events = [
        {"type": "delta", "text": "答案 [1]。"},
        {"type": "usage", "model": "mock", "latency_ms": 1, "prompt_tokens": 2,
         "completion_tokens": 3},
    ]
    return [
        patch.object(route, "vector_index", index),
        patch.object(route, "embedding_service", SimpleNamespace(state="ready", embed_query=lambda q: [1])),
        patch.object(route, "llm_chat", MagicMock(return_value={
            "answer": "答案 [1]。", "model": "mock", "latency_ms": 1,
            "prompt_tokens": 2, "completion_tokens": 3,
        })),
        patch.object(route, "llm_stream_chat", lambda question, sources, history=None: iter(stream_events)),
        patch.object(route, "llm_gate", MagicMock(acquire=MagicMock(return_value=True),
                                                   release=MagicMock())),
        patch.object(route, "audit", SimpleNamespace(log_audit=MagicMock())),
        patch.object(route.rt, "get_all", return_value={"queries_per_minute": 100, "top_k": 3}),
        patch.object(route.query_limiter, "allow", return_value=(True, 0)),
        patch.object(route.acl, "effective_scope", side_effect=lambda _db, _user, requested: {1, 2, 3}),
        patch.object(route, "_require_ready_documents", lambda *args: None),
        patch.object(route.settings, "context_continuation", switches.get("continuation", False)),
        patch.object(route.settings, "evidence_completion", switches.get("completion", False)),
        patch.object(route.settings, "history_auxiliary", False),
        patch.object(route.settings, "rerank_enabled", False),
    ]


def run_query(database: sqlite3.Connection, question: str, chunk_ids: list[int],
              switches: dict[str, bool], *, stream: bool = False) -> tuple[object, list[str]]:
    context = patches(fake_index(chunk_ids), switches)
    for item in context:
        item.start()
    try:
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        user = SimpleNamespace(id=1, username="persist-tester")
        response = route.query(QueryBody(question=question), request, stream=stream,
                               db=database, user=user)
        lines: list[str] = []
        if stream:
            async def consume() -> list[str]:
                collected = []
                async for chunk in response.body_iterator:
                    collected.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
                return collected
            lines = asyncio.run(consume())
        return response, lines
    finally:
        for item in reversed(context):
            item.stop()


def chat_rows(database: sqlite3.Connection, question: str) -> tuple[int, int, list[float | None]]:
    chat = database.execute("SELECT id FROM chats WHERE question=? ORDER BY id DESC LIMIT 1",
                            (question,)).fetchone()
    if chat is None:
        return 0, 0, []
    scores = [row["score"] for row in database.execute(
        "SELECT score FROM chat_sources WHERE chat_id=? ORDER BY id", (chat["id"],)
    )]
    return chat["id"], len(scores), scores


def test_new_schema_nullable() -> None:
    print("\n== 新库 chat_sources.score 允许为空 ==")
    database = setup_db()
    columns = {row["name"]: row for row in database.execute("PRAGMA table_info(chat_sources)")}
    check(columns["score"]["notnull"] == 0, "新库 score 列允许 NULL")
    database.close()


def test_old_schema_migration() -> None:
    print("\n== 旧库升级：NOT NULL 迁移为可空且保留历史行 ==")
    old = sqlite3.connect(":memory:")
    old.executescript(
        "CREATE TABLE chats (id INTEGER PRIMARY KEY);"
        "CREATE TABLE documents (id INTEGER PRIMARY KEY);"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY);"
        "CREATE TABLE chat_sources (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,"
        " document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,"
        " chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,"
        " score REAL NOT NULL, page INTEGER, paragraph INTEGER, excerpt TEXT NOT NULL);"
    )
    old.execute("INSERT INTO chats(id) VALUES(1)")
    old.execute("INSERT INTO documents(id) VALUES(1)")
    old.execute("INSERT INTO chat_sources(chat_id, document_id, score, excerpt) VALUES(1,1,0.75,'旧来源')")
    old.commit()
    db_module._ensure_chat_source_score_nullable(old)
    columns = {row[1]: row for row in old.execute("PRAGMA table_info(chat_sources)")}
    check(columns["score"][3] == 0, "旧库 score 迁移后可空")
    row = old.execute("SELECT score, excerpt FROM chat_sources").fetchone()
    check(row == (0.75, "旧来源"), "旧行分数与摘录保留")
    old.execute("INSERT INTO chat_sources(chat_id, document_id, score, excerpt) VALUES(1,1,NULL,'补证来源')")
    inserted = old.execute("SELECT score FROM chat_sources ORDER BY id DESC LIMIT 1").fetchone()
    check(inserted == (None,), "迁移后写入 NULL 分数成功")
    old.close()


def test_continuation_persistence() -> None:
    print("\n== 只开续段：普通问答保存完整来源且 score 可为空 ==")
    database = setup_db()
    response, _ = run_query(database, "KEEP-CONT：Byte1 参数是什么", [10],
                            {"continuation": True})
    sources = response["sources"]
    check(len(sources) == 2, f"普通问答返回 2 条来源（实际 {len(sources)}）")
    check(any(source.get("score") is None for source in sources), "续段来源分数为 None")
    chat_id, count, scores = chat_rows(database, "KEEP-CONT：Byte1 参数是什么")
    check(count == len(sources), f"chat_sources 保存全部来源（{count} vs {len(sources)}）")
    check(None in scores, "数据库中续段来源 score 为 NULL（未伪造相似度）")
    history, hidden = route._sources_for_chat(database, chat_id)
    check(len(history) == len(sources) and not hidden, "历史来源可完整恢复")
    database.close()


def test_completion_persistence() -> None:
    print("\n== 只开字面补证：普通问答保存完整来源且 score 可为空 ==")
    database = setup_db()
    question = "KEEP-COMP：Input Monitor Time 的索引和单位"
    response, _ = run_query(database, question, [21], {"completion": True})
    sources = response["sources"]
    check(len(sources) >= 2, f"补证后至少 2 条来源（实际 {len(sources)}）")
    check(any(source.get("score") is None for source in sources), "补证/表头来源分数为 None")
    _, count, scores = chat_rows(database, question)
    check(count == len(sources), f"chat_sources 保存全部来源（{count} vs {len(sources)}）")
    check(None in scores, "数据库补证来源 score 为 NULL")
    database.close()


def test_stream_persistence() -> None:
    print("\n== 只开续段：流式问答正常结束且来源完整 ==")
    database = setup_db()
    question = "KEEP-STREAM：Byte1 参数是什么"
    _, lines = run_query(database, question, [10], {"continuation": True}, stream=True)
    joined = "".join(lines)
    check('"type": "done"' in joined, "流式返回 done 事件")
    check('"type": "error"' not in joined, "流式没有保存/校验错误")
    _, count, scores = chat_rows(database, question)
    check(count == 2, f"流式保存 2 条来源（实际 {count}）")
    check(None in scores, "流式保存的续段 score 为 NULL")
    database.close()


def test_rollback_on_failure() -> None:
    print("\n== 来源保存失败时整轮回滚，不留下成功残记录 ==")
    database = setup_db()
    question = "KEEP-ROLLBACK：会失败的保存"
    before = database.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"]
    try:
        route._store_chat(
            database, 1, question, "答案", "ok", None,
            model="mock", retrieval_ms=1,
            sources=[{"document_id": 999, "chunk_id": None, "score": 0.5, "content": "坏来源"}],
            document_ids=[],
        )
    except sqlite3.IntegrityError:
        pass
    except Exception as exc:  # noqa: BLE001
        check(False, f"预期 FK 约束错误，实际 {exc.__class__.__name__}: {exc}")
    after = database.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"]
    check(after == before, "失败后 chats 不增加（整轮回滚）")
    leaked = database.execute("SELECT COUNT(*) AS n FROM chat_sources WHERE excerpt='坏来源'").fetchone()["n"]
    check(leaked == 0, "失败后不留下来源残行")
    orphan = database.execute("SELECT COUNT(*) AS n FROM conversations WHERE title=?", (question[:30],)).fetchone()["n"]
    check(orphan == 0, "失败后不留下新建会话")
    database.close()


def main() -> int:
    test_new_schema_nullable()
    test_old_schema_migration()
    test_continuation_persistence()
    test_completion_persistence()
    test_stream_persistence()
    test_rollback_on_failure()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
