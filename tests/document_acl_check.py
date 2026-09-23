"""文档级授权的越权测试（服务端 HTTP 接口层）。

ROADMAP 要求："须有服务端直接接口越权测试和管理员例外权限审计"。

关键设计：本测试走**真实的 FastAPI 依赖链**。只替换「当前用户从哪来」这一个来源
（current_user_or_none）与数据库连接，**保留真实的 require_user / require_kb_admin /
require_root**，因此角色校验是真的在跑。

为什么必须这样：直接调用端点函数会**完全绕过 Depends()**，那样写出来的"越权测试"
即使实现里少了角色校验也会通过——是假绿。这个坑本测试的第一版就踩过。

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


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def main() -> None:
    saved = (settings.data_dir, settings.db_path, settings.upload_dir, settings.models_dir)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        root_dir = Path(temp)
        settings.data_dir = root_dir
        settings.db_path = root_dir / "acl.db"
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

            def add_doc(name: str, visibility: str, stored: str) -> int:
                cur = db.execute(
                    "INSERT INTO documents (filename, stored_name, content_type, size_bytes, sha256,"
                    " status, visibility, created_at, updated_at) VALUES (?,?,?,?,?,'ready',?,?,?)",
                    (name, stored, "text/plain", 10, f"sha-{name}", visibility, now, now),
                )
                return int(cur.lastrowid)

            shared_doc = add_doc("设备协议.txt", "shared", "s1.bin")
            secret_doc = add_doc("产品缺陷跟踪表.txt", "restricted", "s2.bin")
            garbage_doc = add_doc("脏数据.txt", "not-a-real-value", "s3.bin")
            db.commit()
            for stored in ("s1.bin", "s2.bin", "s3.bin"):
                (settings.upload_dir / stored).write_bytes(b"dummy")

            ids = {r["username"]: int(r["id"]) for r in db.execute("SELECT id, username FROM users")}
            people = {
                "root": CurrentUser(ids["root"], "root", "root", True),
                "kbadmin": CurrentUser(ids["kbadmin"], "kbadmin", "kb_admin", True),
                "alice": CurrentUser(ids["alice"], "alice", "user", True),
                "bob": CurrentUser(ids["bob"], "bob", "user", True),
            }

            from app.main import app  # noqa: PLC0415

            state = {"user": people["bob"]}
            # 只换「认证来源」，保留真实的角色依赖；不启动 lifespan（不建真库、不校验配置）。
            app.dependency_overrides[current_user_or_none] = lambda: state["user"]
            app.dependency_overrides[get_db] = lambda: db
            client = TestClient(app)

            def as_user(name: str):
                state["user"] = people[name]
                return client

            # ---------- A. fail-closed ----------
            print("\n== fail-closed：可见范围取非法值时按受限处理 ==")
            check(
                acl.normalize_visibility("not-a-real-value") == acl.RESTRICTED,
                "非法 visibility 归一化为 restricted",
            )

            # ---------- B. 文档列表 ----------
            print("\n== GET /api/documents：受限文档不得出现在未授权账号的可选项里 ==")
            r = as_user("bob").get("/api/documents")
            bob_ids = {x["id"] for x in r.json()["items"]}
            check(r.status_code == 200 and secret_doc not in bob_ids, "bob 看不到受限文档")
            check(shared_doc in bob_ids, "bob 能看到共享文档")
            check(
                garbage_doc not in bob_ids,
                "取值为脏数据的文档对普通用户不可见（写坏不会变成公开）",
            )

            r = as_user("root").get("/api/documents")
            check(secret_doc in {x["id"] for x in r.json()["items"]}, "root 可以看到受限文档")
            r = as_user("kbadmin").get("/api/documents")
            check(
                secret_doc in {x["id"] for x in r.json()["items"]},
                "kb_admin 可以看到受限文档（否则无法管理）",
            )

            # ---------- C. 限定范围越权（走真实 HTTP） ----------
            print("\n== POST /api/query 限定范围：不可见文档按「不存在」处理 ==")
            r = as_user("bob").post("/api/query", json={"question": "有哪些缺陷？", "document_ids": [secret_doc]})
            check(r.status_code == 404, f"bob 限定受限文档 → HTTP {r.status_code}（期望 404）")
            check(
                "不存在" in str(r.json().get("detail", "")),
                "错误信息不区分「不存在」与「不可见」，不泄露文档存在性",
            )

            # ---------- D. 授权后可见，撤权后立刻不可见 ----------
            print("\n== 授权 / 撤权立即生效 ==")
            acl.set_document_access(db, secret_doc, acl.RESTRICTED, [ids["alice"]], [], ids["root"], now)
            db.commit()
            r = as_user("alice").get("/api/documents")
            check(secret_doc in {x["id"] for x in r.json()["items"]}, "授权后 alice 能看到受限文档")
            r = as_user("bob").get("/api/documents")
            check(secret_doc not in {x["id"] for x in r.json()["items"]}, "同一时间 bob 仍看不到")

            # ---------- E. 撤权后历史对话不可回读 ----------
            print("\n== 撤权后：历史对话的答案与引用都必须隐藏 ==")
            cur = db.execute(
                "INSERT INTO conversations (user_id, title, document_ids, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (ids["alice"], "缺陷查询", f"[{secret_doc}]", now, now),
            )
            conversation_id = int(cur.lastrowid)
            cur = db.execute(
                "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, created_at)"
                " VALUES (?,?,1,?,?,'ok',?)",
                (ids["alice"], conversation_id, "有哪些缺陷？", "缺陷A：划伤；缺陷B：偏移。", now),
            )
            chat_id = int(cur.lastrowid)
            db.execute(
                "INSERT INTO chat_sources (chat_id, document_id, chunk_id, score, page, excerpt)"
                " VALUES (?,?,NULL,0.9,1,?)",
                (chat_id, secret_doc, "缺陷A：划伤"),
            )
            db.commit()

            body = as_user("alice").get(f"/api/conversations/{conversation_id}").json()
            check(not body["turns"][0].get("redacted"), "撤权前：alice 能读到该轮")
            check("缺陷A" in body["turns"][0]["answer"], "撤权前答案正文可见")

            acl.set_document_access(db, secret_doc, acl.RESTRICTED, [], [], ids["root"], now)
            db.execute("UPDATE documents SET visibility='restricted' WHERE id=?", (secret_doc,))
            db.commit()

            body = as_user("alice").get(f"/api/conversations/{conversation_id}").json()
            turn = body["turns"][0]
            check(turn.get("redacted") is True, "撤权后该轮被标记为已隐藏")
            check("缺陷A" not in turn["answer"], "撤权后答案正文不再返回（不残留原文内容）")
            check(turn["sources"] == [], "撤权后引用来源为空")
            check(secret_doc not in body.get("document_ids", []), "对话限定范围也不再回显该文档 id")

            single = as_user("alice").get(f"/api/chats/{chat_id}").json()
            check(single.get("redacted") is True, "单条问答接口同样被隐藏")
            check("缺陷A" not in single["answer"], "单条问答的答案正文不再返回")

            # ---------- F. 权限管理接口的角色校验（真实依赖链） ----------
            print("\n== 可见范围管理：角色校验必须真的生效 ==")
            r = as_user("kbadmin").put(
                f"/api/admin/documents/{secret_doc}/access",
                json={"visibility": "shared", "user_ids": []},
            )
            check(r.status_code == 403, f"kb_admin 修改可见范围 → HTTP {r.status_code}（期望 403）")

            r = as_user("bob").get(f"/api/admin/documents/{secret_doc}/access")
            check(r.status_code == 403, f"普通用户读取授权名单 → HTTP {r.status_code}（期望 403）")

            r = as_user("root").put(
                f"/api/admin/documents/{secret_doc}/access",
                json={"visibility": "restricted", "user_ids": []},
            )
            check(r.status_code == 422, f"受限但空名单 → HTTP {r.status_code}（期望 422）")

            r = as_user("root").put(
                f"/api/admin/documents/{secret_doc}/access",
                json={"visibility": "restricted", "user_ids": [], "group_ids": [9999]},
            )
            check(r.status_code == 422, f"授权给不存在的用户组 → HTTP {r.status_code}（期望 422）")

            r = as_user("root").put(
                f"/api/admin/documents/{secret_doc}/access",
                json={"visibility": "restricted", "user_ids": [9999]},
            )
            check(r.status_code == 422, f"授权给不存在的用户 → HTTP {r.status_code}（期望 422）")

            r = as_user("root").put(
                f"/api/admin/documents/{secret_doc}/access",
                json={"visibility": "restricted", "user_ids": [ids["alice"]]},
            )
            check(
                r.status_code == 200 and r.json()["granted_user_ids"] == [ids["alice"]],
                "root 设置授权名单成功",
            )

            r = as_user("root").get(f"/api/admin/documents/{secret_doc}/access")
            detail = r.json()
            check(detail["visibility"] == "restricted", "读回可见范围为 restricted")
            check(
                ids["alice"] in {c["id"] for c in detail["candidates"]},
                "可授权候选人包含 alice",
            )
            check(
                ids["root"] not in {c["id"] for c in detail["candidates"]},
                "root 不出现在可授权候选人里（本来就不受限）",
            )

            # ---------- G. 上传可见范围的权限边界 ----------
            print("\n== 上传：kb_admin 不得上传受限文档 ==")
            r = as_user("kbadmin").post(
                "/api/admin/documents",
                files={"file": ("t.txt", b"hello", "text/plain")},
                data={"version": "1.0", "visibility": "restricted"},
            )
            check(r.status_code == 403, f"kb_admin 上传受限文档 → HTTP {r.status_code}（期望 403）")

            # ---------- H. 按组授权 ----------
            print("\n== 按组授权：一次授权给一批人 ==")
            r = as_user("kbadmin").post("/api/admin/groups", json={"name": "质量部"})
            check(r.status_code == 403, f"kb_admin 创建用户组 → HTTP {r.status_code}（期望 403）")
            r = as_user("bob").get("/api/admin/groups")
            check(r.status_code == 403, f"普通用户读取用户组 → HTTP {r.status_code}（期望 403）")

            r = as_user("root").post(
                "/api/admin/groups", json={"name": "质量部", "description": "质量与工艺"}
            )
            check(r.status_code == 201, "root 创建用户组成功")
            group_id = r.json()["id"]
            r = as_user("root").post("/api/admin/groups", json={"name": "质量部"})
            check(r.status_code == 409, f"重名用户组 → HTTP {r.status_code}（期望 409）")

            r = as_user("root").put(
                f"/api/admin/groups/{group_id}/members", json={"user_ids": [ids["bob"]]}
            )
            check(
                r.status_code == 200 and r.json()["member_ids"] == [ids["bob"]],
                "把 bob 加入用户组",
            )

            # 只授权给组、不给任何个人：验证"受限必须有可见对象"的规则不会误伤这种用法
            r = as_user("root").put(
                f"/api/admin/documents/{secret_doc}/access",
                json={"visibility": "restricted", "user_ids": [], "group_ids": [group_id]},
            )
            check(
                r.status_code == 200 and r.json()["granted_group_ids"] == [group_id],
                "只授权给用户组（不带任何个人账号）也被接受",
            )

            r = as_user("bob").get("/api/documents")
            check(
                secret_doc in {x["id"] for x in r.json()["items"]},
                "组内成员 bob 通过组获得访问权",
            )
            r = as_user("alice").get("/api/documents")
            check(
                secret_doc not in {x["id"] for x in r.json()["items"]},
                "非组内成员 alice 仍然看不到",
            )
            r = as_user("bob").post(
                "/api/query", json={"question": "有哪些缺陷？", "document_ids": [secret_doc]}
            )
            check(r.status_code != 404, f"bob 可通过组授权限定该文档（HTTP {r.status_code}，不再是 404）")

            # 造一轮 bob 引用该文档的对话，用于验证"移出组"之后的隐藏
            cur = db.execute(
                "INSERT INTO conversations (user_id, title, document_ids, created_at, updated_at)"
                " VALUES (?,?,?,?,?)",
                (ids["bob"], "bob 的缺陷查询", f"[{secret_doc}]", now, now),
            )
            bob_conversation = int(cur.lastrowid)
            cur = db.execute(
                "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, created_at)"
                " VALUES (?,?,1,?,?,'ok',?)",
                (ids["bob"], bob_conversation, "有哪些缺陷？", "缺陷C：划伤。", now),
            )
            bob_chat = int(cur.lastrowid)
            db.execute(
                "INSERT INTO chat_sources (chat_id, document_id, chunk_id, score, page, excerpt)"
                " VALUES (?,?,NULL,0.9,1,?)",
                (bob_chat, secret_doc, "缺陷C：划伤"),
            )
            db.commit()

            body = as_user("bob").get(f"/api/conversations/{bob_conversation}").json()
            check(not body["turns"][0].get("redacted"), "在组内时：bob 能读到该轮")

            r = as_user("root").put(f"/api/admin/groups/{group_id}/members", json={"user_ids": []})
            check(r.status_code == 200, "把 bob 移出用户组")
            r = as_user("bob").get("/api/documents")
            check(
                secret_doc not in {x["id"] for x in r.json()["items"]},
                "移出组后 bob 立即失去访问权（组成员关系现查，不缓存）",
            )
            body = as_user("bob").get(f"/api/conversations/{bob_conversation}").json()
            check(body["turns"][0].get("redacted") is True, "移出组后历史对话同步隐藏")
            check("缺陷C" not in body["turns"][0]["answer"], "移出组后答案正文不再返回")

            # 删除组必须清理它的文档授权，否则会留下悬空授权
            r = as_user("root").delete(f"/api/admin/groups/{group_id}")
            check(r.status_code == 204, "root 删除用户组")
            left = db.execute(
                "SELECT COUNT(*) AS n FROM document_acl WHERE subject_type=? AND subject_id=?",
                (acl.SUBJECT_GROUP, group_id),
            ).fetchone()["n"]
            check(left == 0, "删除组时一并清理了它残留的文档授权（无悬空行）")
            r = as_user("root").get(f"/api/admin/documents/{secret_doc}/access")
            check(r.json()["granted_group_ids"] == [], "文档的组授权已清空")

            # ---------- I. 管理员例外审计 ----------
            print("\n== 管理员打开受限原文必须留下例外审计 ==")
            r = as_user("kbadmin").get(f"/api/documents/{secret_doc}/file")
            check(r.status_code == 200, f"kb_admin 可打开受限原文 → HTTP {r.status_code}")
            row = db.execute(
                "SELECT detail FROM audit_logs WHERE action='document_open' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            check(
                row is not None and "管理员例外" in (row["detail"] or ""),
                "审计里标注了「管理员例外」",
            )

            r = as_user("root").get(f"/api/documents/{shared_doc}/file")
            row = db.execute(
                "SELECT detail FROM audit_logs WHERE action='document_open' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            check(
                "管理员例外" not in (row["detail"] or ""),
                "打开共享文档时不会误标为例外",
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
