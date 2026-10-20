"""知识库 MCP 小服务（第三轮）：stdio、只读，复用 Web 问答的检索/排序/引用路径。

对应交接文档「第三轮：MCP 小服务（方向已确认）」的最小范围：
- 文档列表；
- 带 document_ids 的检索；
- 按来源身份取原文；
- 需要问答工具时复用已有生成路径，并明确区分原始证据与生成回答。

实现约束：
- 使用 MCP Python SDK v2（`mcp>=2.3`，本文件按 v2 的 MCPServer 接口编写），stdio 传输；
- 不复制索引、不另写检索/排序逻辑：直接调用 `app.routers.query.retrieve_sources`，
  与网页问答共用同一份范围约束、检索排序和来源引用；
- 不提供命令执行、文件删除、重建索引等写操作；
- 本机单用户只读服务：使用与服务相同的 RAG_DATA_DIR/RAG_DB_PATH 等配置，
  工具调用不写入 conversations/chats（不产生个人问答历史）。

运行：
    python -m pip install -r requirements-mcp.txt     # 仅额外安装 MCP SDK
    python scripts\\mcp_server.py                       # stdio，供 MCP 客户端拉起

客户端配置示例（stdio）：
    {"mcpServers": {"personal-kb": {
        "command": "<项目目录>\\.venv\\Scripts\\python.exe",
        "args": ["<项目目录>\\scripts\\mcp_server.py"],
        "env": {"RAG_DATA_DIR": "<项目目录>\\data"}
    }}}
"""
from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可操作提示
    raise SystemExit(
        "未安装 MCP SDK。请先安装 requirements-mcp.txt："
        "python -m pip install -r requirements-mcp.txt"
    ) from exc

from app import db as db_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.embeddings import EmbeddingUnavailable, embedding_service  # noqa: E402
from app.gate import llm_gate  # noqa: E402
from app.index import vector_index  # noqa: E402
from app.llm import LLMError, chat as llm_chat  # noqa: E402
from app.reranking import RerankUnavailable  # noqa: E402
from app.routers.query import retrieve_sources  # noqa: E402

SERVER_NAME = "personal-kb"
SERVER_VERSION = "0.1.0"
MAX_TOP_K = 20
MAX_QUESTION_CHARS = 2000
READY_WAIT_SECONDS = float(os.environ.get("MCP_READY_WAIT_SECONDS", "240"))


class KnowledgeBaseUnavailable(RuntimeError):
    """知识库/嵌入模型未就绪时抛出的可读错误。"""


class KnowledgeBase:
    """惰性初始化：只在第一次工具调用时打开数据库/加载索引/等待嵌入模型。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ready = False

    def ensure_ready(self) -> None:
        with self._lock:
            if self._ready:
                return
            db_module.init_db()
            with closing(db_module.connect()) as db:
                vector_index.reload(db)
            embedding_service.start()
            deadline = time.monotonic() + READY_WAIT_SECONDS
            while embedding_service.state in ("idle", "loading") and time.monotonic() < deadline:
                time.sleep(0.1)
            if embedding_service.state != "ready":
                raise KnowledgeBaseUnavailable(
                    "嵌入模型未就绪："
                    + (embedding_service.message or embedding_service.state)
                    + "。请确认服务配置（RAG_DATA_DIR / RAG_MODELS_DIR）后重试。"
                )
            self._ready = True

    def connect(self):
        return db_module.connect()


kb = KnowledgeBase()


# ---------------------------------------------------------------------------
# 共享的校验/序列化（工具函数与测试都直接调用这里）
# ---------------------------------------------------------------------------


def _normalize_query(query: str) -> str:
    text = (query or "").strip()
    if not text:
        raise ValueError("query 不能为空")
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(f"query 过长（最多 {MAX_QUESTION_CHARS} 字）")
    return text


def _normalize_top_k(top_k: int) -> int:
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise ValueError("top_k 必须是整数")
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k 需在 1-{MAX_TOP_K} 之间")
    return top_k


def _ready_documents(db) -> list[dict]:
    rows = db.execute(
        "SELECT id, filename, version FROM documents WHERE status='ready' ORDER BY filename, id"
    ).fetchall()
    return [
        {"document_id": int(row["id"]), "filename": row["filename"], "version": row["version"]}
        for row in rows
    ]


def _resolve_scope(db, document_ids: list[int] | None) -> tuple[set[int] | None, list[int]]:
    """空/None = 全部就绪文档；显式列表必须是全部就绪文档的子集。

    返回 (scope, scope_ids)：scope 为 None 表示全库；scope_ids 为回显的有效文档列表。
    """
    ready = {item["document_id"] for item in _ready_documents(db)}
    if document_ids is None:
        return None, []
    normalized = sorted({int(item) for item in document_ids})
    if any(item <= 0 for item in normalized):
        raise ValueError("document_ids 必须为正整数")
    unknown = [item for item in normalized if item not in ready]
    if unknown:
        raise ValueError(f"文档不存在或尚未就绪：{unknown}；请先用 list_documents 核对")
    if not normalized:
        return None, []
    return set(normalized), normalized


def _source_payload(source: dict, *, include_content: bool) -> dict:
    payload = {
        "chunk_id": source.get("chunk_id"),
        "document_id": source["document_id"],
        "filename": source["filename"],
        "version": None,
        "page": source.get("page"),
        "paragraph": source.get("paragraph"),
        "score": source.get("score"),
        "excerpt": " ".join((source.get("content") or "").split())[:300],
    }
    if include_content:
        payload["content"] = source.get("content")
    return payload


def _with_versions(db, payloads: list[dict]) -> list[dict]:
    versions = {
        int(row["id"]): row["version"]
        for row in db.execute("SELECT id, version FROM documents")
    }
    for payload in payloads:
        payload["version"] = versions.get(int(payload["document_id"]))
    return payloads


def _format_pages(rows) -> list[dict]:
    pages: list[dict] = []
    index: dict[object, dict] = {}
    for row in rows:
        page = row["page"]
        key = ("page", page) if page is not None else ("page", None)
        if key not in index:
            index[key] = {"page": page, "paragraphs": []}
            pages.append(index[key])
        index[key]["paragraphs"].append(
            {"paragraph": row["paragraph"], "content": row["content"]}
        )
    return pages


# ---------------------------------------------------------------------------
# MCP 工具实现
# ---------------------------------------------------------------------------


def list_documents() -> dict:
    """列出知识库中所有已就绪文档（id、文件名、版本）。

    只返回 status='ready' 的文档；检索与取原文工具都使用这些 document_id。
    """
    try:
        kb.ensure_ready()
        with closing(kb.connect()) as db:
            documents = _ready_documents(db)
    except KnowledgeBaseUnavailable as exc:
        raise ToolError(str(exc)) from exc
    return {
        "documents": documents,
        "total": len(documents),
        "note": "仅列出已就绪文档；本地单用户只读服务，不含权限过滤。",
    }


def search_documents(query: str, document_ids: list[int] | None = None, top_k: int = 5) -> dict:
    """按问题检索知识库，返回带来源身份的原始片段（文件名/页码/版本/片段内容）。

    document_ids 省略或为空表示全库检索；提供时只在该范围内检索，越界的文档 ID 会报错。
    返回的 score 只是检索排序分数，不是正确率；content 是原始证据，不是生成回答。
    """
    try:
        text = _normalize_query(query)
        limit = _normalize_top_k(top_k)
        kb.ensure_ready()
        with closing(kb.connect()) as db:
            scope, scope_ids = _resolve_scope(db, document_ids)
            sources, retrieval_ms, empty_reason = retrieve_sources(db, text, scope, limit)
            results = _with_versions(
                db, [_source_payload(source, include_content=True) for source in sources]
            )
    except (ValueError, KnowledgeBaseUnavailable, EmbeddingUnavailable, RerankUnavailable) as exc:
        raise ToolError(str(exc)) from exc
    response = {
        "query": text,
        "scope_mode": "selected" if scope is not None else "all",
        "document_ids": scope_ids,
        "results": results,
        "retrieval_ms": retrieval_ms,
        "note": "score 是检索排序分数，不是正确率；content 为原始片段，需按文件名/页码核对。",
    }
    if not results:
        response["empty_reason"] = empty_reason or "no_hits"
        response["note"] = "没有找到可用来源；不会退化成全库或编造答案。"
    return response


def get_document_text(document_id: int, page: int | None = None) -> dict:
    """按来源身份取原文：指定文档（可选指定页码）的原始文字，不做模型生成。

    页码来自入库时的解析结果（PDF 为页号；TXT/Markdown 可能没有页码）。
    """
    try:
        document_id = int(document_id)
        if document_id <= 0:
            raise ValueError("document_id 必须为正整数")
        if page is not None:
            page = int(page)
            if page < 1:
                raise ValueError("page 必须从 1 开始")
        kb.ensure_ready()
        with closing(kb.connect()) as db:
            document = db.execute(
                "SELECT id, filename, version FROM documents WHERE id=? AND status='ready'",
                (document_id,),
            ).fetchone()
            if document is None:
                raise ValueError(f"文档不存在或尚未就绪：{document_id}")
            sql = "SELECT seq, page, paragraph, content FROM chunks WHERE document_id=?"
            params: list = [document_id]
            if page is not None:
                sql += " AND page=?"
                params.append(page)
            sql += " ORDER BY seq, id"
            rows = db.execute(sql, params).fetchall()
            if not rows:
                suffix = f"第 {page} 页" if page is not None else ""
                raise ValueError(
                    f"文档 {document_id} 没有{suffix}可用文本；扫描页可能尚未做 OCR。"
                )
            pages = _format_pages(rows)
    except (ValueError, KnowledgeBaseUnavailable) as exc:
        raise ToolError(str(exc)) from exc
    return {
        "document_id": int(document["id"]),
        "filename": document["filename"],
        "version": document["version"],
        "requested_page": page,
        "pages": pages,
        "note": "原始文字，未经模型生成；页码/段落来自入库解析，可用于核对引用。",
    }


def ask_question(question: str, document_ids: list[int] | None = None, top_k: int = 5) -> dict:
    """基于知识库证据生成回答；answer 是生成内容，sources 是原始证据，两者分开返回。

    没有检索到证据时只返回 no_evidence 状态，不会用模型常识补答。
    document_ids 省略/为空表示全库；范围约束与网页问答相同。
    """
    try:
        text = _normalize_query(question)
        limit = _normalize_top_k(top_k)
        kb.ensure_ready()
        with closing(kb.connect()) as db:
            scope, scope_ids = _resolve_scope(db, document_ids)
            sources, retrieval_ms, empty_reason = retrieve_sources(db, text, scope, limit)
            evidence = _with_versions(
                db, [_source_payload(source, include_content=False) for source in sources]
            )
    except (ValueError, KnowledgeBaseUnavailable, EmbeddingUnavailable, RerankUnavailable) as exc:
        raise ToolError(str(exc)) from exc
    if not sources:
        return {
            "status": "no_evidence",
            "question": text,
            "scope_mode": "selected" if scope is not None else "all",
            "document_ids": scope_ids,
            "answer": None,
            "sources": [],
            "empty_reason": empty_reason or "no_hits",
            "note": "没有找到可用证据；未生成回答，也不会改用模型常识补齐。",
        }
    if not llm_gate.acquire(90.0):
        raise ToolError("系统繁忙：并发模型请求已达上限，请稍后重试")
    try:
        result = llm_chat(text, sources, None)
    except LLMError as exc:
        raise ToolError(f"生成模型调用失败（{exc.code}）：{exc.message}") from exc
    finally:
        llm_gate.release()
    return {
        "status": "ok",
        "question": text,
        "scope_mode": "selected" if scope is not None else "all",
        "document_ids": scope_ids,
        "answer": result.get("answer"),
        "model": result.get("model"),
        "latency_ms": result.get("latency_ms"),
        "retrieval_ms": retrieval_ms,
        "sources": evidence,
        "notice": (
            "answer 是生成回答，不保证正确；sources 是原始证据（文件名/页码/版本），"
            "请逐条核对。检索分数不是正确率。"
        ),
    }


# ---------------------------------------------------------------------------
# MCP 服务器
# ---------------------------------------------------------------------------

server = MCPServer(
    name=SERVER_NAME,
    version=SERVER_VERSION,
    instructions=(
        "个人知识库只读查询服务：可列文档、按文档范围检索、按来源身份取原文、"
        "基于证据生成回答。所有来源都带文件名/页码/版本；检索分数不是正确率。"
        "本服务不提供命令执行、文件删除或重建索引。"
    ),
)
server.add_tool(list_documents)
server.add_tool(search_documents)
server.add_tool(get_document_text)
server.add_tool(ask_question)


def main() -> None:
    # stdio 传输要求 stdout 只用于协议消息；诊断信息一律写 stderr。
    print(
        f"{SERVER_NAME} MCP server v{SERVER_VERSION}（stdio，只读）"
        f" 数据目录={settings.data_dir} 嵌入后端={settings.embed_backend}",
        file=sys.stderr,
    )
    server.run("stdio")


if __name__ == "__main__":
    main()
