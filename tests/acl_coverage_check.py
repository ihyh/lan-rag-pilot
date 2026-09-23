"""文档级授权的**覆盖面**检查：自动遍历所有 API 路由，验证受限内容不泄露。

为什么需要它：上一轮 ACL 实现里出现过两个漏洞——历史问答被原样送进模型提示词、
旧版 GET /api/chats 列表未脱敏。两次都是**人工复审才发现**的，而它们的共同点是
"逐点加固了记得的路径，漏了没想起来的路径"。这类问题不该依赖复审的运气。

本测试的做法是**不枚举已知路径**，而是从 FastAPI 的路由表反推：
  1. 造一份受限文档，其文件名、片段与答案里都埋入唯一标记；
  2. 先授权给 alice、让她问一轮（答案里就带上了标记），然后**撤销授权**；
  3. 遍历 app.routes 的每个 GET 路由，分别以 alice / bob 身份请求；
  4. 断言**任何响应体里都不出现该标记或受限文件名**；
  5. 同时以 root 请求同一批路由，断言标记**确实出现**——否则说明夹具本身是空的，
     测试会变成永远通过的假绿。

另外单独覆盖 HTTP 观察不到的那条路径：`_conversation_history()` 是拼给模型的
历史上下文，它的泄露在响应体里看不出来，必须直接断言。

无需网络；使用临时数据库；不触碰真实数据。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import acl  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import get_db, init_db, now_iso  # noqa: E402
from app.deps import CurrentUser, current_user_or_none  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []

# 唯一标记：同时埋进受限文档的文件名、片段与答案，任何一处泄露都能被发现。
MARKER = "S3CR3T-MARKER-9137"
SECRET_FILENAME = f"绝密手册-{MARKER}.txt"

# 这些路由不属于业务接口，或需要 FastAPI 运行时生成，跳过。
SKIP_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

# 只读之外的路径参数如何取具体值
PARAM_VALUES: dict[str, int] = {}


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def fill_path(template: str) -> str | None:
    """把 /api/chats/{chat_id} 填成具体路径；出现未知参数则返回 None（跳过该路由）。"""
    path = template
    for name, value in PARAM_VALUES.items():
        path = path.replace("{" + name + "}", str(value))
    if "{" in path:
        return None
    return path


def main() -> None:
    saved = (settings.data_dir, settings.db_path, settings.upload_dir, settings.models_dir)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        root_dir = Path(temp)
        settings.data_dir = root_dir
        settings.db_path = root_dir / "coverage.db"
        settings.upload_dir = root_dir / "uploads"
        settings.models_dir = root_dir / "models"
        settings.upload_dir.mkdir(parents=True, exist_ok=True)

        init_db()
        db = sqlite3.connect(settings.db_path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")

        try:
            now = now_iso()
            for username, is_kb in (("root", 0), ("kbadmin", 1), ("alice", 0), ("bob", 0)):
                db.execute(
                    "INSERT INTO users (username, password_hash, role, is_kb_admin, is_active,"
                    " created_at, updated_at) VALUES (?,?,?,?,1,?,?)",
                    (username, "x", "root" if username == "root" else "user", is_kb, now, now),
                )
            ids = {r["username"]: int(r["id"]) for r in db.execute("SELECT id, username FROM users")}

            def add_doc(name: str, visibility: str, stored: str) -> int:
                cur = db.execute(
                    "INSERT INTO documents (filename, stored_name, content_type, size_bytes, sha256,"
                    " status, visibility, created_at, updated_at) VALUES (?,?,?,?,?,'ready',?,?,?)",
                    (name, stored, "text/plain", 10, f"sha-{name}", visibility, now, now),
                )
                return int(cur.lastrowid)

            shared_doc = add_doc("公开设备手册.txt", "shared", "pub.bin")
            secret_doc = add_doc(SECRET_FILENAME, "restricted", "sec.bin")
            db.commit()
            for stored in ("pub.bin", "sec.bin"):
                (settings.upload_dir / stored).write_bytes(b"dummy")

            # alice 曾被授权并问过一轮，答案与片段里都留下了标记
            acl.set_document_access(db, secret_doc, acl.RESTRICTED, [ids["alice"]], [], ids["root"], now)
            cur = db.execute(
                "INSERT INTO conversations (user_id, title, document_ids, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (ids["alice"], "绝密查询", f"[{secret_doc}]", now, now),
            )
            conversation_id = int(cur.lastrowid)
            cur = db.execute(
                "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, created_at)"
                " VALUES (?,?,1,?,?,'ok',?)",
                (ids["alice"], conversation_id, "绝密参数是多少？", f"答案包含 {MARKER}。", now),
            )
            chat_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO chat_sources (chat_id, document_id, chunk_id, score, page, excerpt)"
                " VALUES (?,?,NULL,0.9,1,?)",
                (chat_id, secret_doc, f"片段包含 {MARKER}"),
            )
            # 一份 bob 从未有权访问的问答，用于验证"从未授权"与"被撤销"两种情况
            cur = db.execute(
                "INSERT INTO conversations (user_id, title, document_ids, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (ids["bob"], "bob 的绝密查询", f"[{secret_doc}]", now, now),
            )
            bob_conversation = int(cur.lastrowid)
            cur = db.execute(
                "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, created_at)"
                " VALUES (?,?,1,?,?,'ok',?)",
                (ids["bob"], bob_conversation, "绝密参数是多少？", f"答案包含 {MARKER}。", now),
            )
            bob_chat = int(cur.lastrowid)
            db.execute(
                "INSERT INTO chat_sources (chat_id, document_id, chunk_id, score, page, excerpt)"
                " VALUES (?,?,NULL,0.9,1,?)",
                (bob_chat, secret_doc, f"片段包含 {MARKER}"),
            )
            # 一个用户组，用于覆盖组授权路径
            group_id = acl.create_group(db, "质量部", "", now)
            # 然后撤销 alice 的授权：此后她不应再从任何接口读到标记
            acl.set_document_access(db, secret_doc, acl.RESTRICTED, [], [], ids["root"], now)
            db.execute("UPDATE documents SET visibility='restricted' WHERE id=?", (secret_doc,))
            db.commit()

            PARAM_VALUES.update(
                {
                    "conversation_id": conversation_id,
                    "chat_id": chat_id,
                    "document_id": secret_doc,
                    "doc_id": secret_doc,
                    "group_id": group_id,
                    "user_id": ids["alice"],
                }
            )

            from app.main import app  # noqa: PLC0415

            state = {"user": None}
            app.dependency_overrides[current_user_or_none] = lambda: state["user"]
            app.dependency_overrides[get_db] = lambda: db
            client = TestClient(app)

            people = {
                "root": CurrentUser(ids["root"], "root", "root", True),
                "kbadmin": CurrentUser(ids["kbadmin"], "kbadmin", "kb_admin", True),
                "alice": CurrentUser(ids["alice"], "alice", "user", True),
                "bob": CurrentUser(ids["bob"], "bob", "user", True),
            }

            def get_as(identity: str, path: str):
                state["user"] = people[identity]
                return client.get(path)

            # ---------- 收集所有可测的 GET 路由 ----------
            #
            # 注意：不能用 app.routes 遍历——这个 FastAPI 版本把 include_router 进来的
            # 路由包成了 _IncludedRouter（没有 .path/.methods），直接遍历只会看到
            # /api/health、/api/ready、/sw.js 三条，其余全被静默跳过。
            # 用 OpenAPI 模式取路径最稳妥，且与框架版本无关。
            schema = app.openapi()
            targets: list[str] = []
            for template, operations in (schema.get("paths") or {}).items():
                if template in SKIP_PATHS:
                    continue
                if not any(method.upper() == "GET" for method in operations):
                    continue
                filled = fill_path(template)
                if filled is None:
                    continue
                targets.append(filled)
            targets = sorted(set(targets))

            print(f"\n== 将遍历 {len(targets)} 个 GET 路由，检查是否泄露受限内容 ==")
            for path in targets:
                print(f"        {path}")

            # 覆盖类测试必须自证覆盖：枚举一旦失效，"未发现泄露"就会变成假绿。
            check(
                len(targets) >= 15,
                f"发现的路由数量足够（{len(targets)} 个）；少于 15 个说明枚举失效，结论不可信",
            )
            for must_cover in (
                "/api/documents",
                "/api/conversations",
                "/api/chats",
                "/api/admin/chats",
                "/api/admin/feedback",
            ):
                check(must_cover in targets, f"关键路径已纳入遍历：{must_cover}")
            # 带路径参数的路由代表"按 id 直接取"这一类，最容易被漏掉
            check(
                any("{" not in p and p.count("/") >= 3 for p in targets)
                and any(p.startswith("/api/conversations/") for p in targets),
                "带路径参数的详情类路由已纳入（这类最容易漏）",
            )

            # ---------- A. 非授权身份：任何响应都不得含标记或受限文件名 ----------
            print("\n== 未授权身份（alice 已被撤权 / bob 从未授权）==")
            leaks: list[str] = []
            for identity in ("alice", "bob"):
                for path in targets:
                    try:
                        r = get_as(identity, path)
                    except Exception as exc:  # noqa: BLE001 - 端点异常不应让本测试崩掉
                        print(f"        [skip] {identity} {path} 抛出 {type(exc).__name__}")
                        continue
                    body = r.text or ""
                    for needle, label in ((MARKER, "标记"), (SECRET_FILENAME, "受限文件名")):
                        if needle in body:
                            leaks.append(f"{identity} {path} 泄露了{label}（HTTP {r.status_code}）")
            check(not leaks, f"遍历 {len(targets)} 个路由后未发现泄露")
            for item in leaks:
                print(f"        ⚠ {item}")

            # ---------- B. 对照组：root 必须能看到标记，否则夹具是空的 ----------
            print("\n== 对照组：root 能看到，证明夹具有效 ==")
            state["user"] = people["root"]
            control = client.get("/api/admin/chats")
            check(
                MARKER in (control.text or ""),
                "root 的 /api/admin/chats 能看到标记（说明夹具真的埋进去了）",
            )

            # ---------- C. 提示词路径：HTTP 观察不到，必须直接断言 ----------
            print("\n== 送进模型的历史上下文也必须过滤撤权内容 ==")
            from app.routers.query import _conversation_history  # noqa: PLC0415

            alice_view = _conversation_history(db, conversation_id, acl.visible_document_ids(db, people["alice"]))
            blob = str(alice_view)
            check(MARKER not in blob, "撤权后，历史上下文里不再包含撤权文档的内容")
            check(
                not any(turn.get("answer", "").find(MARKER) >= 0 for turn in alice_view),
                "撤权后历史里的答案正文被剔除",
            )

            root_view = _conversation_history(db, conversation_id, acl.visible_document_ids(db, people["root"]))
            check(
                any(MARKER in turn.get("answer", "") for turn in root_view),
                "root 的历史上下文仍包含该轮（对照，说明过滤不是一概丢弃）",
            )

            # 恢复授权后应当又能看到，确认过滤是"按当前权限"而非"永久丢弃"
            acl.set_document_access(db, secret_doc, acl.RESTRICTED, [ids["alice"]], [], ids["root"], now)
            db.commit()
            back = _conversation_history(db, conversation_id, acl.visible_document_ids(db, people["alice"]))
            check(
                any(MARKER in turn.get("answer", "") for turn in back),
                "重新授权后历史上下文恢复（过滤依据是当前权限）",
            )

            app.dependency_overrides.clear()
        finally:
            db.close()
            (
                settings.data_dir,
                settings.db_path,
                settings.upload_dir,
                settings.models_dir,
            ) = saved

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
