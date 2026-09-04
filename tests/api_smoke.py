"""端到端冒烟测试（需应用与 mock DeepSeek 均已启动，见 docs 测试手册）。

先准备两个进程：
  1) mock 模型:      uvicorn tests.mock_deepseek:app --port 8099
  2) 应用(离线模式):  按 tests/smoke_runner.sh 内的环境变量设置后
     uvicorn app.main:app --port 8090
     （Linux/Docker 环境下可直接：docker run --rm -v "$PWD:/rag" -w /rag <含依赖镜像> bash tests/smoke_runner.sh）

然后：.venv\\Scripts\\python tests\\api_smoke.py
覆盖测试计划中的：登录/错误密码/注销、user 权限 403、root 全部管理、
四种格式（使用脚本生成的最小有效样本）、扫描 PDF、重复文件、超限、伪造扩展名、空文件、
问答与来源、历史可见性、限流 429、LLM 异常不泄露(HTTP 层)、审计。
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE_URL = os.environ.get("RAG_SMOKE_URL", "http://127.0.0.1:8090")
ROOT_PW = os.environ.get("RAG_ROOT_PASSWORD", "fcd123")

PASS: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        PASS.append(msg)
        print(f"  [PASS] {msg}")
    else:
        FAIL.append(msg)
        print(f"  [FAIL] {msg}")


def err_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        detail = data.get("detail")
        if isinstance(detail, dict):
            return f"{detail.get('code','')}:{detail.get('message','')}"
        return str(detail)
    except Exception:
        return resp.text[:200]


class Smoke:
    def __init__(self) -> None:
        self.c = httpx.Client(base_url=BASE_URL, timeout=60.0)
        self.root: dict = {}
        self.alice: dict = {}
        self.bob: dict = {}

    def login(self, username: str, password: str) -> httpx.Response:
        return self.c.post("/api/login", json={"username": username, "password": password})

    # ---------- 1. 认证 ----------
    def test_auth(self) -> None:
        print("\n== 认证 ==")
        r = self.c.get("/login")
        check(r.status_code == 200 and "登录" in r.text, "登录页面可访问")
        r = self.c.get("/static/manifest.webmanifest")
        check(r.status_code == 200 and r.json().get("name"), "Web App Manifest 可访问")
        r = self.c.get("/static/icons/icon.svg")
        check(r.status_code == 200 and "<svg" in r.text, "应用图标可访问")
        r = self.c.get("/sw.js")
        check(
            r.status_code == 200 and r.headers.get("service-worker-allowed") == "/",
            "Service Worker 入口及作用域正确",
        )
        r = self.login("root", "wrong-password")
        check(r.status_code == 401, "错误密码返回 401")
        r = self.login("root", ROOT_PW)
        check(r.status_code == 200, "root/fcd123 可登录")
        self.root = r.json()["user"]
        check(self.root.get("role") == "root", "root 角色正确")
        r = self.c.get("/api/me")
        check(r.status_code == 200 and r.json().get("username") == "root", "/api/me 正常")
        # 密码不以明文落库：登录用任意大密码不应成功且不泄露
        r = self.c.get("/api/health")
        check(r.status_code == 200, "健康检查 200")
        r = self.c.get("/api/ready")
        check(r.status_code == 200 and r.json().get("status") == "ready", "就绪检查 200")
        with sqlite3.connect(os.environ["RAG_DB_PATH"]) as db:
            password_hash = db.execute(
                "SELECT password_hash FROM users WHERE username='root'"
            ).fetchone()[0]
            session_hash = db.execute(
                "SELECT token_hash FROM sessions ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        check(password_hash.startswith("$argon2") and password_hash != ROOT_PW, "root 密码仅以 Argon2 哈希保存")
        check(session_hash != self.c.cookies.get("rag_session"), "会话令牌仅以哈希保存")

    # ---------- 2. user 权限 ----------
    def test_user_permissions(self) -> None:
        print("\n== user 权限（403）==")
        r = self.c.post(
            "/api/admin/users",
            json={"username": "alice", "password": "alice123", "role": "user"},
        )
        check(r.status_code == 201, "root 创建 user 成功")
        r = self.login("alice", "alice123")
        check(r.status_code == 200, "alice 可登录")
        self.alice = r.json()["user"]

        r = self.c.post("/api/admin/documents", files={"file": ("x.txt", b"data", "text/plain")})
        check(r.status_code == 403, "user 上传返回 403")
        r = self.c.post("/api/admin/users", json={"username": "x", "password": "x123456", "role": "user"})
        check(r.status_code == 403, "user 建账号返回 403")
        r = self.c.get("/api/admin/audit")
        check(r.status_code == 403, "user 看审计返回 403")
        r = self.c.get("/api/admin/feedback.csv")
        check(r.status_code == 403, "user 导出反馈返回 403")
        r = self.c.post("/api/admin/documents/1/reindex")
        check(r.status_code == 403, "user 重建索引返回 403")
        r = self.c.delete("/api/admin/documents/1")
        check(r.status_code == 403, "user 删除文档返回 403")
        r = self.c.get("/api/admin/users")
        check(r.status_code == 403, "user 管理用户返回 403")
        r = self.c.get("/api/knowledge-bases")
        check(r.status_code == 200 and r.json().get("total", 0) >= 1, "user 可查看自己的知识库范围")
        r = self.c.post("/api/admin/departments", json={"name": "不应创建"})
        check(r.status_code == 403, "user 创建部门返回 403")
        r = self.c.patch("/api/admin/documents/1/knowledge-bases", json={"knowledge_base_ids": [1]})
        check(r.status_code == 403, "user 修改文档知识库权限返回 403")

    # ---------- 3. 文档入库（格式/重复/伪造/空/超限） ----------
    @staticmethod
    def _txt_bytes() -> bytes:
        paras = []
        for i in range(24):
            paras.append(
                f"第 {i + 1} 节 局域网考勤管理规定：员工应在工作日 9:00 前完成打卡登记，"
                "迟到需在系统内填写迟到说明并由直属主管审批；出差前需提交申请，"
                "市内交通凭票据据实报销，出差住宿每日上限 400 元。"
            )
        return ("\n\n".join(paras)).encode("utf-8")

    @staticmethod
    def _docx_bytes() -> bytes:
        from docx import Document

        doc = Document()
        doc.add_paragraph("局域网运维手册（测试文档）")
        for i in range(30):
            doc.add_paragraph(
                f"第 {i + 1} 条：机房温度应保持在 18-27 摄氏度；UPS 电池每季度巡检一次；"
                "核心交换机配置变更需提前一天审批并留存审计记录。"
            )
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    @staticmethod
    def _pdf_bytes() -> bytes:
        text = "Pilot policy: hotel reimbursement limit is 400 yuan per day."
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
        xref = len(out)
        out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
        for offset in offsets[1:]:
            out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        out.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
        return bytes(out)

    @staticmethod
    def _blank_pdf_bytes() -> bytes:
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    def test_documents(self) -> None:
        print("\n== 文档管理（root）==")
        r = self.login("root", ROOT_PW)
        check(r.status_code == 200, "文档管理前重新登录 root")
        txt = self._txt_bytes()
        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("考勤与报销规定.txt", txt, "text/plain")},
        )
        check(r.status_code == 201 and r.json().get("status") == "ready", "TXT 上传并入库成功")
        self.txt_doc = r.json()
        check(int(self.txt_doc["num_chunks"]) >= 1, f"TXT 切片数 {self.txt_doc['num_chunks']} >= 1")

        docx = self._docx_bytes()
        r = self.c.post(
            "/api/admin/documents",
            files={
                "file": (
                    "运维手册.docx",
                    docx,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        check(r.status_code == 201 and r.json().get("status") == "ready", "DOCX 上传并入库成功")
        self.docx_doc = r.json()

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("制度说明.md", b"# Knowledge base\n\nMarkdown pilot document with unique content.", "text/markdown")},
        )
        check(r.status_code == 201 and r.json().get("status") == "ready", "Markdown 上传并入库成功")

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("报销规则.pdf", self._pdf_bytes(), "application/pdf")},
        )
        check(r.status_code == 201 and r.json().get("pages") == 1, "文本 PDF 上传并保留页码")

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("扫描件.pdf", self._blank_pdf_bytes(), "application/pdf")},
        )
        check(r.status_code == 400 and "OCR" in err_text(r), "无文本 PDF 明确提示不含 OCR")

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("考勤与报销规定-copy.txt", txt, "text/plain")},
        )
        check(r.status_code == 409, "重复文件(SHA-256)返回 409")

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("伪装.pdf", txt, "application/pdf")},
        )
        check(r.status_code == 400 and "PDF" in err_text(r), "伪造扩展名返回 400")

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("错误类型.txt", b"plain text", "image/png")},
        )
        check(r.status_code == 400 and "MIME" in err_text(r), "错误 MIME 返回 400")

        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("空文件.txt", b"", "text/plain")},
        )
        check(r.status_code == 400, "空文件返回 400")

        big = b"x" * (25 * 1024 * 1024 + 1024)
        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("超大文件.txt", big, "text/plain")},
        )
        check(r.status_code == 413, "超过 25MB 返回 413")

        r = self.c.get("/api/admin/documents")
        check(r.status_code == 200 and len(r.json()["items"]) >= 2, "文档列表包含已入库文件")

        # 文档删除后再查列表
        target = self.docx_doc["id"]
        r = self.c.delete(f"/api/admin/documents/{target}")
        check(r.status_code == 204, "root 删除文档 204")
        r = self.c.get("/api/admin/documents")
        check(all(d["id"] != target for d in r.json()["items"]), "删除后列表同步")

        # 重新索引（重新上传一个再测）
        r = self.c.post(
            "/api/admin/documents",
            files={"file": ("运维手册2.docx", self._docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        doc2 = r.json()
        r = self.c.post(f"/api/admin/documents/{doc2['id']}/reindex")
        check(r.status_code == 200 and r.json().get("status") == "ready", "重新索引成功")

    # ---------- 4. 问答 ----------
    def test_query(self) -> None:
        print("\n== 问答 ==")
        r = self.c.post("/api/query", json={"question": "出差住宿上限是多少？"})
        check(r.status_code == 200, "root 问答成功")
        body = r.json()
        check(body.get("answer") and "Mock 模型" in body["answer"], "返回答案")
        check(len(body.get("sources", [])) > 0, f"来源数 {len(body.get('sources', []))} > 0")
        src = body["sources"][0]
        check(src.get("filename") and ("page" in src or "paragraph" in src), "来源含文件名与位置")
        self.root_chat_id = body["chat_id"]

        # 部门范围隔离：无匹配部门时只能得到明确拒答，不能看到默认知识库文档
        r = self.login("root", ROOT_PW)
        default_dep = next(
            (d for d in self.c.get("/api/admin/departments").json()["items"] if d["name"] == "默认部门"),
            None,
        )
        r = self.c.post("/api/admin/departments", json={"name": "隔离测试部门"})
        isolated_dep_id = r.json().get("id") if r.status_code == 201 else None
        alice_id = next(u["id"] for u in self.c.get("/api/admin/users").json()["items"] if u["username"] == "alice")
        r = self.c.patch(f"/api/admin/users/{alice_id}", json={"department_ids": [isolated_dep_id]}) if isolated_dep_id else r
        check(r.status_code == 200 if isolated_dep_id else False, "root 可设置隔离测试部门")
        r = self.login("alice", "alice123")
        r = self.c.post("/api/query", json={"question": "尝试访问无权限知识库"})
        check(r.status_code == 200 and not r.json().get("sources") and "没有可访问" in r.json().get("answer", ""), "无部门权限时只返回明确拒答")
        r = self.login("root", ROOT_PW)
        r = self.c.patch(f"/api/admin/users/{alice_id}", json={"department_ids": [default_dep["id"]] if default_dep else []})
        check(r.status_code == 200, "恢复 alice 默认部门权限")

        # user 也可问答
        r = self.login("alice", "alice123")
        check(r.status_code == 200, "问答前切换为 alice(user)")
        r = self.c.post("/api/query", json={"question": "打卡时间是几点？"})
        check(r.status_code == 200, "alice(user) 问答成功")
        self.alice_chat_id = r.json()["chat_id"]
        r = self.c.post(f"/api/chats/{self.alice_chat_id}/feedback", json={"rating": "helpful"})
        check(r.status_code == 200 and r.json().get("rating") == "helpful", "user 提交回答反馈成功")
        r = self.c.get(f"/api/chats/{self.alice_chat_id}")
        check(r.status_code == 200 and r.json().get("feedback", {}).get("rating") == "helpful", "问答详情包含本人反馈")
        r = self.c.post(f"/api/chats/{self.root_chat_id}/feedback", json={"rating": "unhelpful"})
        check(r.status_code == 404, "user 不能评价他人问答")

    def test_history(self) -> None:
        print("\n== 历史与可见性 ==")
        r = self.c.get("/api/chats")
        items = r.json()["items"]
        check(r.status_code == 200 and any(c["id"] == self.alice_chat_id for c in items), "本人历史可见")
        r = self.c.get(f"/api/chats/{self.root_chat_id}")
        check(r.status_code == 404, "user 看不到他人问答记录(404)")
        r = self.c.get(f"/api/chats/{self.alice_chat_id}")
        check(r.status_code == 200 and len(r.json().get("sources", [])) > 0, "本人问答详情含来源")
        # 登出 alice 后会话失效
        r = self.c.post("/api/logout")
        check(r.status_code == 200, "注销成功")
        r = self.c.get("/api/me")
        check(r.status_code == 401, "注销后会话立即失效")

    # ---------- 5. root 审计与全局可见 ----------
    def test_admin_views(self) -> None:
        print("\n== root 审计 ==")
        r = self.login("root", ROOT_PW)
        check(r.status_code == 200, "重新登录 root")
        r = self.c.get("/api/admin/chats")
        check(r.status_code == 200 and any(c["id"] == self.root_chat_id for c in r.json()["items"]), "root 可见全部问答")
        r = self.c.get("/api/admin/audit")
        acts = [a["action"] for a in r.json()["items"]]
        check("llm_query" in acts and "doc_upload" in acts and "feedback_submit" in acts and "login" in acts, "审计含 llm_query/doc_upload/feedback/login")
        r = self.c.get("/api/admin/feedback")
        check(r.status_code == 200 and any(f["chat_id"] == self.alice_chat_id for f in r.json()["items"]), "root 可查看用户反馈")
        r = self.c.get("/api/admin/feedback.csv")
        check(r.status_code == 200 and "feedback_id" in r.text, "root 可导出用户反馈 CSV")
        r = self.c.get("/api/admin/departments")
        check(r.status_code == 200 and any(d["name"] == "默认部门" for d in r.json()["items"]), "默认部门已初始化")
        r = self.c.get("/api/admin/knowledge-bases")
        check(r.status_code == 200 and any(k["name"] == "默认知识库" for k in r.json()["items"]), "默认知识库已初始化")
        r = self.c.get("/api/admin/overview")
        check(r.status_code == 200 and r.json()["counts"]["users"] >= 2, "概览计数正常")
        r = self.c.patch(
            "/api/admin/settings",
            json={"top_k": 4, "queries_per_minute": 11, "max_concurrent_llm": 2},
        )
        check(r.status_code == 200 and r.json()["settings"]["top_k"] == 4, "root 可修改并持久化运行参数")

    # ---------- 6. 限流 ----------
    def test_rate_limit(self) -> None:
        print("\n== 限流 ==")
        r = self.c.post(
            "/api/admin/users",
            json={"username": "bob", "password": "bob123456", "role": "user"},
        )
        check(r.status_code == 201, "创建 bob")
        self.c.post("/api/login", json={"username": "bob", "password": "bob123456"})
        hit = False
        for i in range(12):
            r = self.c.post("/api/query", json={"question": f"第 {i} 次测试问题"})
            if r.status_code == 429:
                hit = True
                break
        check(hit, "第 11 次问答触发 429 限流")

    def test_concurrency(self) -> None:
        print("\n== 5 用户并发问答 ==")
        r = self.login("root", ROOT_PW)
        check(r.status_code == 200, "并发测试前重新登录 root")
        accounts = [(f"load{i}", f"loadpass{i}") for i in range(5)]
        for username, password in accounts:
            r = self.c.post(
                "/api/admin/users",
                json={"username": username, "password": password, "role": "user"},
            )
            check(r.status_code == 201, f"创建并发用户 {username}")

        def ask(account: tuple[str, str]) -> int:
            username, password = account
            with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
                login = client.post("/api/login", json={"username": username, "password": password})
                if login.status_code != 200:
                    return login.status_code
                return client.post("/api/query", json={"question": f"{username} 查询住宿上限"}).status_code

        with ThreadPoolExecutor(max_workers=5) as pool:
            statuses = list(pool.map(ask, accounts))
        check(statuses == [200] * 5, f"5 用户并发均成功：{statuses}")

    # ---------- 7. LLM 异常不泄露 ----------
    def test_llm_errors(self) -> None:
        print("\n== LLM 异常 ==")
        r = self.login("alice", "alice123")
        check(r.status_code == 200, "alice 重新登录（避免被限流用户影响）")
        for trigger, code in [
            ("[[mock:http500]]", "llm_upstream"),
            ("[[mock:http401]]", "llm_auth"),
            ("[[mock:http429]]", "llm_rate_limited"),
            ("[[mock:sleep:2]]", "llm_timeout"),
        ]:
            r = self.c.post("/api/query", json={"question": trigger})
            body = r.json().get("detail", {})
            check(
                r.status_code == 502 and body.get("code") == code,
                f"{trigger} -> 502 code={body.get('code')}",
            )
            check(
                "mock-key" not in (r.text or "") and "Bearer" not in (r.text or ""),
                "异常响应不泄露 API Key",
            )
        # 无余额
        r = self.c.post("/api/query", json={"question": "[[mock:http402]]"})
        body = r.json().get("detail", {})
        check(r.status_code == 502 and body.get("code") == "llm_quota", "http402 -> llm_quota")

    # ---------- 8. 用户管理 ----------
    def test_user_admin(self) -> None:
        print("\n== 用户管理 ==")
        r = self.login("root", ROOT_PW)
        check(r.status_code == 200, "root 重新登录")
        users = self.c.get("/api/admin/users").json()["items"]
        alice = next(u for u in users if u["username"] == "alice")
        r = self.c.patch(f"/api/admin/users/{alice['id']}", json={"is_active": False})
        check(r.status_code == 200, "root 停用 alice")
        self.c.post("/api/logout")
        r = self.login("alice", "alice123")
        check(r.status_code == 403, "停用用户登录返回 403")
        r = self.login("root", ROOT_PW)
        check(r.status_code == 200, "root 再次登录")
        r = self.c.patch(f"/api/admin/users/{alice['id']}", json={"is_active": True})
        check(r.status_code == 200, "重新启用 alice")
        r = self.c.patch(
            f"/api/admin/users/{alice['id']}", json={"password": "newpass123"}
        )
        check(r.status_code == 200, "root 重置 alice 密码")
        with sqlite3.connect(os.environ["RAG_DB_PATH"]) as db:
            sessions = db.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id=?", (alice["id"],)
            ).fetchone()[0]
        check(sessions == 0, "重置密码后撤销 alice 的全部旧会话")

    def finish(self) -> None:
        r = self.login("root", ROOT_PW)
        r = self.c.post("/api/logout")
        self.c.close()
        print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
        if FAIL:
            print("失败项:")
            for f in FAIL:
                print(f"  - {f}")
            sys.exit(1)


def main() -> None:
    print(f"目标: {BASE_URL}")
    s = Smoke()
    s.test_auth()
    s.test_user_permissions()
    s.test_documents()
    s.test_query()
    s.test_history()
    s.test_admin_views()
    s.test_rate_limit()
    s.test_concurrency()
    s.test_llm_errors()
    s.test_user_admin()
    s.finish()


if __name__ == "__main__":
    main()
