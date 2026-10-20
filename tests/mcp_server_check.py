"""知识库 MCP 小服务协议级检查（第三轮验收）。

需要单独安装 `requirements-mcp.txt`；未安装 MCP SDK 时打印 SKIP 并以 0 退出，
保证默认 `tests/run_checks.py` 不需要 MCP 依赖。

检查内容（使用临时数据库、mock 嵌入，不使用私有文档、不启动监听端口）：
- MCP 客户端完成 initialize、列工具、调用工具；
- 文档列表 / 带 document_ids 的检索 / 按来源身份取原文；
- 未知文档、空查询、非法 top_k、缺失页等错误处理；
- 范围过滤：限定文档后结果不越界；
- 与网页问答同一问题/范围下的来源一致（复用同一检索路径）；
- 问答工具区分"生成回答"与"原始证据"，且不写入个人问答历史；
- 不暴露命令执行/删除/重建索引等写工具。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

PASS: list[str] = []
FAIL: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        PASS.append(message)
        print(f"  [PASS] {message}")
    else:
        FAIL.append(message)
        print(f"  [FAIL] {message}")


def skip(reason: str) -> int:
    print(f"[SKIP] {reason}")
    return 0


def _tool_payload(result) -> dict:
    """兼容 MCP SDK v2 的结构化内容与纯文本内容两种返回形式。"""
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"无法解析工具返回：{result!r}")


def _tool_text(result) -> str:
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            return text
    return str(result)


def _is_error(result) -> bool:
    return bool(getattr(result, "isError", False) or getattr(result, "is_error", False))


def seed_database(root: Path) -> dict:
    """用临时 SQLite + mock 嵌入写入两篇相似内容文档（与网页问答对照用）。"""
    from app import db as db_module
    from app.embeddings import embedding_service
    from app.index import vector_index

    db_module.init_db()
    embedding_service.start()
    now = db_module.now_iso()
    database = db_module.connect()
    try:
        documents = [
            (1, "scope-1.txt", "scope-probe 住宿上限 400 元。第一页正文内容。"),
            (2, "scope-2.txt", "scope-probe 住宿上限 600 元。另一份相似文档。"),
        ]
        for document_id, filename, content in documents:
            database.execute(
                "INSERT INTO documents(id,filename,stored_name,content_type,size_bytes,sha256,"
                "status,created_at,updated_at) VALUES(?,?,?,?,?,?,'ready',?,?)",
                (document_id, filename, f"stored-{document_id}", "text/plain",
                 len(content.encode("utf-8")), str(document_id), now, now),
            )
            vector = embedding_service.embed_texts([content])[0].tobytes()
            database.execute(
                "INSERT INTO chunks(document_id,seq,page,paragraph,token_count,content,vector) "
                "VALUES(?,?,?,?,?,?,?)",
                (document_id, 0, 1, 1, len(content), content, vector),
            )
        extra = "scope-probe 第二页补充说明：设备巡检每季度一次。"
        vector = embedding_service.embed_texts([extra])[0].tobytes()
        database.execute(
            "INSERT INTO chunks(document_id,seq,page,paragraph,token_count,content,vector) "
            "VALUES(?,?,?,?,?,?,?)",
            (1, 1, 2, 1, len(extra), extra, vector),
        )
        database.execute("UPDATE documents SET num_chunks=2, pages=2 WHERE id=1")
        database.execute("UPDATE documents SET num_chunks=1, pages=1 WHERE id=2")
        database.commit()
        vector_index.reload(database)
    finally:
        database.close()
    return {"documents": [item[1] for item in documents]}


def consistency_with_web() -> None:
    """同一问题/文档范围下，网页问答与 MCP 检索的来源一致。"""
    from fastapi.testclient import TestClient

    from app.deps import CurrentUser, current_user_or_none
    from app.main import app as fastapi_app
    from app.routers import query as query_router

    state = {"user": CurrentUser(1, "mcp-check", "root", True)}
    fastapi_app.dependency_overrides[current_user_or_none] = lambda: state["user"]
    original_llm_chat = query_router.llm_chat
    usage = {"answer": "按检索来源回答 [1]", "model": "mcp-check-mock",
             "latency_ms": 1, "prompt_tokens": 1, "completion_tokens": 1}

    def fake_chat(question, sources, history=None):
        return dict(usage)

    query_router.llm_chat = fake_chat
    try:
        with TestClient(fastapi_app) as client:
            response = client.post(
                "/api/query",
                json={"question": "scope-probe 住宿上限", "scope_mode": "selected",
                      "document_ids": [1]},
            )
        check(response.status_code == 200, "网页问答在同一临时数据上可调用（用于对照）")
        web_sources = {
            (source["chunk_id"], source["document_id"], source.get("page"), source.get("paragraph"))
            for source in response.json().get("sources", [])
        }
    finally:
        query_router.llm_chat = original_llm_chat
        fastapi_app.dependency_overrides.clear()
    return web_sources


def apply_r4_mutations(env: dict) -> None:
    """模拟另一个进程在 MCP 会话存活期间新增文档、改状态、重新索引。"""
    from app.db import now_iso
    from app.embeddings import embedding_service

    database = sqlite3.connect(env["RAG_DB_PATH"])
    try:
        now = now_iso()
        content_new = "scope-probe 住宿上限 900 元。新增文档。"
        vector_new = embedding_service.embed_texts([content_new])[0].tobytes()
        database.execute(
            "INSERT INTO documents(id,filename,stored_name,content_type,size_bytes,sha256,"
            "status,created_at,updated_at) VALUES(?,?,?,?,?,?,'ready',?,?)",
            (3, "scope-3.txt", "stored-3", "text/plain",
             len(content_new.encode("utf-8")), "sha-3", now, now),
        )
        database.execute(
            "INSERT INTO chunks(document_id,seq,page,paragraph,token_count,content,vector) "
            "VALUES(?,?,?,?,?,?,?)",
            (3, 0, 1, 1, len(content_new), content_new, vector_new),
        )
        # 旧文档 2 改为非就绪：全库检索必须不再返回它的片段。
        database.execute(
            "UPDATE documents SET status='failed', updated_at=? WHERE id=2", (now,)
        )
        # 文档 1 重新索引：chunks 重建（行数/ID 与内容变化）。
        database.execute("DELETE FROM chunks WHERE document_id=1")
        content_reindexed = "scope-probe 重新索引后的 900 元。"
        vector_reindexed = embedding_service.embed_texts([content_reindexed])[0].tobytes()
        database.execute(
            "INSERT INTO chunks(document_id,seq,page,paragraph,token_count,content,vector) "
            "VALUES(?,?,?,?,?,?,?)",
            (1, 0, 1, 1, len(content_reindexed), content_reindexed, vector_reindexed),
        )
        database.execute(
            "UPDATE documents SET num_chunks=1, updated_at=? WHERE id=1", (now,)
        )
        database.commit()
    finally:
        database.close()


async def protocol_checks(env: dict, seeded: dict) -> dict:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", str(ROOT / "scripts" / "mcp_server.py")],
        env=env,
        cwd=str(ROOT),
    )
    observed: dict = {}
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            observed["init"] = initialized
            server_info = (getattr(initialized, "server_info", None)
                           or getattr(initialized, "serverInfo", None))
            server_name = getattr(server_info, "name", None)
            protocol_version = (getattr(initialized, "protocol_version", "")
                                or getattr(initialized, "protocolVersion", ""))
            check(server_name == "personal-kb",
                  f"MCP initialize 返回服务名 personal-kb（实际 {server_name}）")
            check(bool(protocol_version), f"MCP 协议版本有效（{protocol_version}）")

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            observed["tools"] = sorted(names)
            for expected in ("list_documents", "search_documents", "get_document_text", "ask_question"):
                check(expected in names, f"列出工具 {expected}")
            forbidden = {"delete_document", "reindex", "run_command", "execute", "delete"}
            check(not (names & forbidden), "不暴露命令执行/删除/重建索引工具")

            result = await session.call_tool("list_documents", {})
            documents = _tool_payload(result)
            ids = {item["document_id"] for item in documents["documents"]}
            check(ids == {1, 2}, f"list_documents 返回两份就绪文档（实际 {sorted(ids)}）")
            check(all(item["filename"] for item in documents["documents"]), "文档列表带文件名")

            result = await session.call_tool(
                "search_documents", {"query": "scope-probe 住宿上限", "top_k": 5}
            )
            search = _tool_payload(result)
            observed["unrestricted"] = search
            found_docs = {item["document_id"] for item in search["results"]}
            check(search["scope_mode"] == "all", "未限定 document_ids 时回显全库模式")
            check({1, 2} <= found_docs, f"全库检索召回两份相似文档（实际 {sorted(found_docs)}）")
            check(all(item.get("filename") and "score" in item and "content" in item
                      for item in search["results"]),
                  "检索结果带文件名/片段内容/排序分数")

            result = await session.call_tool(
                "search_documents",
                {"query": "scope-probe 住宿上限", "document_ids": [1], "top_k": 5},
            )
            scoped = _tool_payload(result)
            observed["scoped"] = scoped
            check(scoped["scope_mode"] == "selected" and scoped["document_ids"] == [1],
                  "限定 document_ids 时回显有效范围")
            check(bool(scoped["results"]) and all(
                item["document_id"] == 1 for item in scoped["results"]),
                "限定范围后检索结果不越界")

            result = await session.call_tool(
                "search_documents", {"query": "scope-probe", "document_ids": [999]}
            )
            observed["unknown_document_error"] = _is_error(result)
            check(_is_error(result) and "文档不存在" in _tool_text(result),
                  "不存在的 document_ids 明确报错")

            result = await session.call_tool("search_documents", {"query": "   "})
            check(_is_error(result) and "query" in _tool_text(result), "空查询明确报错")

            result = await session.call_tool(
                "search_documents", {"query": "scope-probe", "top_k": 0}
            )
            check(_is_error(result) and "top_k" in _tool_text(result), "非法 top_k 明确报错")

            result = await session.call_tool("get_document_text", {"document_id": 1})
            text_payload = _tool_payload(result)
            observed["text"] = text_payload
            content = "".join(
                paragraph["content"]
                for page in text_payload["pages"]
                for paragraph in page["paragraphs"]
            )
            check("400" in content and "第二页补充说明" in content,
                  "按来源身份取原文返回完整文本")
            check("原始文字" in text_payload.get("note", ""), "原文工具标明未经模型生成")

            result = await session.call_tool("get_document_text", {"document_id": 1, "page": 2})
            page_payload = _tool_payload(result)
            check([page["page"] for page in page_payload["pages"]] == [2],
                  "指定页码时只返回该页")

            result = await session.call_tool("get_document_text", {"document_id": 1, "page": 99})
            check(_is_error(result) and "没有" in _tool_text(result), "不存在的页码明确报错")

            result = await session.call_tool("get_document_text", {"document_id": 999})
            check(_is_error(result) and "文档不存在" in _tool_text(result),
                  "取原文时未知文档明确报错")

            # ---------- R4：同一 MCP 会话内资料变化必须刷新索引/范围 ----------
            apply_r4_mutations(env)
            result = await session.call_tool(
                "search_documents", {"query": "scope-probe", "document_ids": [3]}
            )
            new_doc = _tool_payload(result)
            check(bool(new_doc["results"]) and all(
                item["document_id"] == 3 for item in new_doc["results"]),
                "R4: 同一会话新增文档后检索立即可用（索引已刷新）")

            result = await session.call_tool("search_documents", {"query": "scope-probe"})
            full_after = _tool_payload(result)
            full_docs = {item["document_id"] for item in full_after["results"]}
            check(2 not in full_docs, "R4: 非就绪文档不再作为全库检索来源")
            check(full_docs and full_docs <= {1, 3},
                  f"R4: 全库检索只返回当前就绪文档（实际 {sorted(full_docs)}）")

            result = await session.call_tool(
                "search_documents", {"query": "scope-probe", "document_ids": [2]}
            )
            check(_is_error(result) and "尚未就绪" in _tool_text(result),
                  "R4: 已改为非就绪的文档在限定检索时明确报错")

            result = await session.call_tool("list_documents", {})
            listed = {item["document_id"] for item in _tool_payload(result)["documents"]}
            check(listed == {1, 3}, f"R4: 文档列表反映最新就绪集合（实际 {sorted(listed)}）")

            result = await session.call_tool(
                "search_documents", {"query": "scope-probe", "document_ids": [1]}
            )
            reindexed = _tool_payload(result)
            observed["scoped_final"] = reindexed
            contents = " ".join(item["content"] or "" for item in reindexed["results"])
            check("重新索引后" in contents,
                  "R4: 同一文档重新索引后检索使用新切片内容")
    return observed


def in_process_ask_check(env: dict) -> None:
    """问答工具：用同一生成路径（mock 模型替换），验证证据/回答分离且不落库。"""
    import mcp_server as mcp_module

    mcp_module.kb.ensure_ready()
    from app import db as db_module

    with closing(db_module.connect()) as database:
        before = database.execute(
            "SELECT (SELECT COUNT(*) FROM conversations) AS c, (SELECT COUNT(*) FROM chats) AS h"
        ).fetchone()
    mcp_module.llm_chat = lambda question, sources, history=None: {
        "answer": "按来源回答：住宿上限见 [1]。",
        "model": "mcp-check-mock",
        "latency_ms": 3,
        "prompt_tokens": 10,
        "completion_tokens": 8,
    }
    payload = mcp_module.ask_question("scope-probe 住宿上限", document_ids=[1])
    check(payload["status"] == "ok", "问答工具在证据可用时返回 status=ok")
    check(payload["answer"] and "[1]" in payload["answer"], "返回生成回答正文")
    check(payload.get("sources") and all(
        source["document_id"] == 1 for source in payload["sources"]),
        "问答工具的来源不越出限定范围")
    check("原始证据" in payload.get("notice", ""),
          "问答工具明确区分生成回答与原始证据")
    check(all("content" not in source for source in payload["sources"]),
          "问答返回的来源只给身份/摘录，正文用取原文工具")
    # R4：同一进程内的问答也必须按最新资料刷新索引（新增文档 3 后）。
    payload_new = mcp_module.ask_question("scope-probe", document_ids=[3])
    check(payload_new["status"] == "ok" and all(
        source["document_id"] == 3 for source in payload_new["sources"]),
        "R4: 问答工具在新增文档后立即使用刷新后的索引")
    with closing(db_module.connect()) as database:
        after = database.execute(
            "SELECT (SELECT COUNT(*) FROM conversations) AS c, (SELECT COUNT(*) FROM chats) AS h"
        ).fetchone()
    check(tuple(before) == tuple(after), "MCP 工具调用不写入个人问答历史")


def main() -> int:
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        return skip(f"未安装 MCP SDK（requirements-mcp.txt）：{exc}")

    work_dir = Path(tempfile.mkdtemp(prefix="mcp-check-"))
    env = dict(os.environ)
    env.update({
        "RAG_DATA_DIR": str(work_dir),
        "RAG_DB_PATH": str(work_dir / "mcp.db"),
        "RAG_UPLOAD_DIR": str(work_dir / "uploads"),
        "RAG_MODELS_DIR": str(work_dir / "models"),
        "RAG_EMBED_BACKEND": "mock",
        "RAG_ROOT_PASSWORD": "mcp-check-root-pw",
        "RAG_SECRET_KEY": "mcp-check-secret-key-0123456789",
        "RAG_QUERIES_PER_MINUTE": "100",
        "RAG_TOP_K": "5",
        "DEEPSEEK_API_KEY": "mock-key",
        "DEEPSEEK_BASE_URL": "http://127.0.0.1:9",
        "DEEPSEEK_MODEL": "mock-model",
        "DEEPSEEK_TIMEOUT_S": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    os.environ.update(env)

    seeded = seed_database(work_dir)
    observed = asyncio.run(protocol_checks(env, seeded))

    # 网页问答对照需要同一进程内复用检索路径；用 R4 变更后的最新范围比较来源身份。
    web_sources = consistency_with_web()
    mcp_sources = {
        (item["chunk_id"], item["document_id"], item.get("page"), item.get("paragraph"))
        for item in observed["scoped_final"]["results"]
    }
    check(bool(web_sources) and web_sources == mcp_sources,
          f"网页问答与 MCP 检索来源一致（web={len(web_sources)} mcp={len(mcp_sources)}）")

    in_process_ask_check(env)
    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项：")
        for item in FAIL:
            print("  - " + item)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
