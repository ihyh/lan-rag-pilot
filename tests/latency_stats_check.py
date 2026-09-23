"""分阶段耗时统计的行为测试。

覆盖两件事：
1. 检索耗时真的被记录，并在作答响应里回传（"为什么慢"不能再靠人工取证）。
2. 概览里的统计口径正确——尤其**拒答与失败不能污染耗时分布**，因为拒答也写 chats
   且 status='ok' 却没有模型调用，若口径写错，百分位会被一堆 0/None 拉偏。

走真实 HTTP 依赖链（只覆盖 current_user_or_none 与 get_db），这样角色校验、
响应结构、SQL 列名都在测试范围内，而不是直接调用内部函数。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RAG_SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("RAG_ROOT_PASSWORD", "test-password")
os.environ.setdefault("DEEPSEEK_API_KEY", "ollama")
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("RAG_EMBED_BACKEND", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app import metrics  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, get_db, init_db, now_iso  # noqa: E402
from app.deps import CurrentUser, current_user_or_none  # noqa: E402
from app.main import app  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def ensure_user(conn: sqlite3.Connection, username: str, role: str) -> int:
    """建一个真实用户行。

    chats.user_id 有外键约束，而我们打开了 PRAGMA foreign_keys=ON——写入问答记录前
    必须存在对应用户，否则测试会因为外键失败而走向与产品无关的报错。
    """
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, is_kb_admin, is_active,"
        " created_at, updated_at)"
        " VALUES (?, 'x', ?, ?, 1, ?, ?)",
        (username, "root" if role == "root" else "user", int(role == "kb_admin"),
         now_iso(), now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def make_client(role: str = "root"):
    """构造走真实依赖链的客户端，只替换"当前用户"和数据库连接。

    连接必须用 app.db.connect()：它设了 isolation_level=None（每条语句自动提交）、
    WAL 与 busy_timeout。手搓 sqlite3.connect 会让路由里的 INSERT 留下未提交事务，
    第二个连接随即 "database is locked"——那是测试环境的假故障，不是产品问题。
    """
    from app.db import connect

    conn = connect()
    user_id = ensure_user(conn, role, role)
    # CurrentUser 的 role 是**逻辑角色**（root / kb_admin / user），与 deps.logical_role 一致。
    user = CurrentUser(user_id, role, role, True)

    def _db():
        yield conn

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[current_user_or_none] = lambda: user
    return TestClient(app), conn, user_id


def insert_chat(conn, user_id, *, status, latency_ms, retrieval_ms, tokens, created_at, question="q"):
    conn.execute(
        "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status,"
        " model, prompt_tokens, completion_tokens, latency_ms, retrieval_ms, created_at)"
        " VALUES (?, NULL, 1, ?, 'a', ?, 'm', 10, ?, ?, ?, ?)",
        (user_id, question, status, tokens, latency_ms, retrieval_ms, created_at),
    )
    conn.commit()


def main() -> None:
    # ---------- A. 纯函数：百分位与汇总口径 ----------
    print("\n== 百分位（最近秩）==")
    check(metrics.percentile([], 50) is None, "空序列返回 None（而不是 0，避免假数据）")
    check(metrics.percentile([7], 50) == 7, "单元素序列任意百分位都是它自己")
    check(metrics.percentile(list(range(1, 11)), 50) == 5, "1..10 的 p50 为 5")
    check(metrics.percentile(list(range(1, 11)), 95) == 10, "1..10 的 p95 为 10（最近秩偏保守）")
    check(metrics.percentile(list(range(1, 11)), 100) == 10, "p100 为最大值")
    check(metrics.percentile(list(range(1, 11)), 0) == 1, "p0 为最小值")
    check(metrics.percentile([30, 10, 20], 50) == 20, "未排序输入也能正确取值")
    for bad in (-1, 101, float("nan"), float("inf")):
        try:
            metrics.percentile([1, 2], bad)
            check(False, f"非法百分位 {bad} 应报错")
        except ValueError:
            check(True, f"非法百分位 {bad!r} 被拒绝")

    print("\n== 汇总口径：拒答与失败不得污染耗时分布 ==")
    rows = [
        {"status": "ok", "latency_ms": 10000, "retrieval_ms": 200, "completion_tokens": 100,
         "created_at": "2026-01-01T00:00:00"},
        {"status": "ok", "latency_ms": 20000, "retrieval_ms": 300, "completion_tokens": 200,
         "created_at": "2026-01-02T00:00:00"},
        # 拒答：status='ok' 但没有模型调用，latency_ms 为 NULL
        {"status": "ok", "latency_ms": None, "retrieval_ms": 150, "completion_tokens": None,
         "created_at": "2026-01-03T00:00:00"},
        # 失败：有部分耗时，但不应进入"成功作答"的耗时分布
        {"status": "error", "latency_ms": 5000, "retrieval_ms": 250, "completion_tokens": 0,
         "created_at": "2026-01-04T00:00:00"},
        # 检索之前就拒答（知识库为空）：两段都没有
        {"status": "ok", "latency_ms": None, "retrieval_ms": None, "completion_tokens": None,
         "created_at": "2026-01-05T00:00:00"},
    ]
    summary = metrics.latency_summary(rows, window=5)
    check(summary["sample_size"] == 2, f"总耗时样本只含 2 次成功作答（实际 {summary['sample_size']}）")
    check(summary["refusals"] == 2, f"拒答单独计数为 2（实际 {summary['refusals']}）")
    check(summary["errors"] == 1, f"失败单独计数为 1（实际 {summary['errors']}）")
    check(
        summary["total_ms"]["max"] == 20000,
        f"失败轮次的 5000ms 没有进入总耗时分布（max={summary['total_ms']['max']}）",
    )
    check(
        summary["total_ms"]["samples"] == 2 and summary["retrieval_ms"]["samples"] == 4,
        "两段样本口径按设计不同：总耗时只含成功作答，检索覆盖所有发生过检索的轮次",
    )
    check(summary["span"] == {"from": "2026-01-01T00:00:00", "to": "2026-01-05T00:00:00"},
          "时间跨度如实反映窗口覆盖范围")
    check(
        "拒答与失败不计入耗时" in summary["note"],
        "note 明确写出统计口径，避免读的人自行猜测",
    )
    check(metrics.latency_summary([])["sample_size"] == 0, "空数据不报错")
    check(metrics.latency_summary([])["total_ms"] is None, "空数据的耗时为 None 而不是 0")

    # ---------- B. 真实 HTTP：检索耗时被记录并回传 ----------
    tmp = Path(tempfile.mkdtemp(prefix="rag-latency-"))
    db_path = tmp / "rag.db"
    # 必须在 init_db() 之前把 settings 指到临时目录：settings.db_path 是在导入时算好的，
    # 事后改环境变量不会生效（会连到真实库上）。
    saved_paths = (settings.data_dir, settings.db_path, settings.upload_dir, settings.models_dir)
    settings.data_dir = tmp
    settings.db_path = db_path
    settings.upload_dir = tmp / "uploads"
    settings.models_dir = tmp / "models"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    client, conn, user_id = make_client()
    try:
        print("\n== 作答响应必须回传分阶段耗时 ==")
        # 造一份可检索的最小语料：用 mock 嵌入后端生成真实维度的向量，
        # 这样 vector_index.reload 真的会把它装进索引（空向量会被跳过）。
        from app.embeddings import embedding_service
        from app.index import vector_index
        from app.routers import query as query_route

        saved_state = (embedding_service.state, embedding_service.message)
        embedding_service.state = "ready"
        embedding_service.message = "就绪"
        content = "风扇异常 E123 的处置步骤：先停机，再检查风扇连接器。"
        vec = embedding_service.embed_texts([content])[0]
        conn.execute(
            "INSERT INTO documents (filename, stored_name, content_type, size_bytes, sha256,"
            " status, num_chunks, visibility, uploaded_by, created_at, updated_at)"
            " VALUES ('m.txt','s.txt','text/plain',10,'sha-1','ready',1,'shared',?,?,?)",
            (user_id, now_iso(), now_iso()),
        )
        doc_id = int(conn.execute("SELECT id FROM documents").fetchone()["id"])
        conn.execute(
            "INSERT INTO chunks (document_id, seq, page, paragraph, token_count, content, vector)"
            " VALUES (?,1,1,1,5,?,?)",
            (doc_id, content, vec.tobytes()),
        )
        conn.commit()
        vector_index.reload(conn)
        check(vector_index.size() > 0, f"测试语料已进入内存索引（{vector_index.size()} 条）")

        # 只替换模型调用：检索段（嵌入 + 混合检索 + 去重）保持真实，那正是被测的部分。
        # 固定返回的耗时让断言可以精确比对，而不是"大于 0 就算过"。
        fake_usage = {"answer": "先停机，再检查风扇连接器。[1]", "model": "mock",
                      "latency_ms": 1234, "prompt_tokens": 40, "completion_tokens": 12}
        with patch.object(query_route, "llm_chat", return_value=fake_usage):
            resp = client.post("/api/query", json={"question": "E123 怎么处置", "stream": False})
        check(resp.status_code == 200, f"提问返回 200（实际 {resp.status_code}）")
        body = resp.json()
        check("retrieval_ms" in body, "响应里带 retrieval_ms")
        check("latency_ms" in body, "响应里带 latency_ms")
        check(body.get("latency_ms") == 1234, f"模型耗时按原值回传（实际 {body.get('latency_ms')!r}）")
        check(
            isinstance(body.get("retrieval_ms"), int) and body["retrieval_ms"] >= 0,
            f"retrieval_ms 是非负整数（实际 {body.get('retrieval_ms')!r}）",
        )

        row = conn.execute(
            "SELECT latency_ms, retrieval_ms, status FROM chats ORDER BY id DESC LIMIT 1"
        ).fetchone()
        check(row is not None, "该轮已写入 chats")
        check(
            row["retrieval_ms"] is not None and row["retrieval_ms"] >= 0,
            f"检索耗时已落库（实际 {row['retrieval_ms']!r}）",
        )
        check(
            row["retrieval_ms"] == body["retrieval_ms"],
            "落库值与响应值一致（不会出现两个真相）",
        )
        check(row["latency_ms"] == 1234, "模型耗时也已落库")

        print("\n== 生成失败也必须留下检索耗时（口径里承诺过）==")
        from app.llm import LLMError

        with patch.object(
            query_route, "llm_chat", side_effect=LLMError("llm_timeout", "模型服务响应超时")
        ):
            failed = client.post("/api/query", json={"question": "E123 怎么处置", "stream": False})
        check(failed.status_code == 502, f"模型失败时返回 502（实际 {failed.status_code}）")
        err_row = conn.execute(
            "SELECT status, latency_ms, retrieval_ms FROM chats ORDER BY id DESC LIMIT 1"
        ).fetchone()
        check(err_row["status"] == "error", "失败轮次标记为 error")
        check(
            err_row["retrieval_ms"] is not None and err_row["retrieval_ms"] >= 0,
            f"失败轮次仍记录了检索耗时（实际 {err_row['retrieval_ms']!r}）——"
            "检索成本与后续是否失败无关",
        )
        check(err_row["latency_ms"] is None, "失败轮次没有模型耗时（本来就没成功调用）")

        audit_row = conn.execute(
            "SELECT detail FROM audit_logs WHERE action='llm_query' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        check(audit_row is not None, "审计里记录了本次问答")
        if audit_row is not None:
            import json as _json

            detail = _json.loads(audit_row["detail"])
            check("retrieval_ms" in detail, "审计明细里也带检索耗时，便于事后核对")

        # ---------- C. 概览接口 ----------
        print("\n== 概览必须给出可判读的耗时统计 ==")
        ov = client.get("/api/admin/overview")
        check(ov.status_code == 200, f"root 可读概览（实际 {ov.status_code}）")
        data = ov.json()
        check("latency" in data, "概览里带 latency 块")
        lat = data.get("latency") or {}
        check(lat.get("window") == metrics.DEFAULT_WINDOW, f"窗口为默认 {metrics.DEFAULT_WINDOW}")
        check(isinstance(lat.get("sample_size"), int), "sample_size 是整数")
        check(lat.get("sample_size", 0) >= 1, "刚才那次真实提问已被统计进去")
        check(
            lat.get("retrieval_ms") is not None and lat["retrieval_ms"]["p50"] is not None,
            "检索耗时统计非空",
        )
        check("note" in lat and lat["note"], "统计口径随响应一起给出")

        print("\n== 概览仍只对 root 开放 ==")
        client2, conn2, _ = make_client(role="user")
        denied = client2.get("/api/admin/overview")
        check(denied.status_code == 403, f"普通用户读概览被拒（实际 {denied.status_code}）")
        conn2.close()

        # ---------- D. 旧库迁移 ----------
        print("\n== 旧数据库必须能自动补列（升级不炸）==")
        # 造"旧库"要真实：完整 schema + 一致的外键，只是 chats 表回退成没有 retrieval_ms
        # 的版本。只造一张 chats 表的假旧库会以外键失败告终，那是测试自身的缺陷。
        legacy = tmp / "legacy.db"
        saved_db = settings.db_path
        settings.db_path = legacy
        try:
            init_db()
            legacy_conn = connect()
            legacy_uid = ensure_user(legacy_conn, "legacy", "root")
            legacy_conn.execute(
                "INSERT INTO chats (user_id, turn_index, question, answer, status, latency_ms,"
                " created_at) VALUES (?, 1, '旧记录', 'a', 'ok', 1234, ?)",
                (legacy_uid, now_iso()),
            )
            # 回退 chats 表：SQLite 不支持 DROP COLUMN 的旧版本也能这样处理，
            # 而且这正是"旧库"的真实形态——列不存在，其余一切正常。
            legacy_conn.execute("PRAGMA foreign_keys = OFF")
            legacy_conn.execute("ALTER TABLE chats RENAME TO chats_prev")
            legacy_conn.execute(
                "CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
                " conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,"
                " turn_index INTEGER NOT NULL DEFAULT 1, question TEXT NOT NULL,"
                " answer TEXT NOT NULL DEFAULT '',"
                " status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),"
                " error TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,"
                " latency_ms INTEGER, created_at TEXT NOT NULL)"
            )
            legacy_conn.execute(
                "INSERT INTO chats (id, user_id, conversation_id, turn_index, question, answer,"
                " status, error, model, prompt_tokens, completion_tokens, latency_ms, created_at)"
                " SELECT id, user_id, conversation_id, turn_index, question, answer, status, error,"
                " model, prompt_tokens, completion_tokens, latency_ms, created_at FROM chats_prev"
            )
            legacy_conn.execute("DROP TABLE chats_prev")
            legacy_conn.execute("PRAGMA foreign_keys = ON")
            legacy_conn.close()

            cols_before = {
                row[1] for row in sqlite3.connect(legacy).execute("PRAGMA table_info(chats)")
            }
            check("retrieval_ms" not in cols_before, "旧库确实没有 retrieval_ms 列")

            init_db()
            init_db()  # 幂等：重复执行不应报错
            after = sqlite3.connect(legacy)
            cols = {row[1] for row in after.execute("PRAGMA table_info(chats)")}
            check("retrieval_ms" in cols, "迁移后补齐了 retrieval_ms 列")
            kept = after.execute("SELECT latency_ms, retrieval_ms FROM chats").fetchone()
            check(
                kept[0] == 1234 and kept[1] is None,
                f"旧数据保留且新列为空（latency={kept[0]}, retrieval={kept[1]}）",
            )
            after.close()
        finally:
            settings.db_path = saved_db
    finally:
        app.dependency_overrides.clear()
        conn.close()
        embedding_service.state, embedding_service.message = saved_state
        (
            settings.data_dir,
            settings.db_path,
            settings.upload_dir,
            settings.models_dir,
        ) = saved_paths

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
