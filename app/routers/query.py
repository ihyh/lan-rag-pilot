"""问答（检索 + 模型调用）与个人问答历史。"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .. import acl, audit, runtime as rt
from ..config import settings
from ..db import connect, get_db, now_iso
from ..deps import require_kb_admin, require_user
from ..embeddings import EmbeddingUnavailable, embedding_service
from ..gate import llm_gate
from ..index import technical_terms, semantic_queries, vector_index
from ..llm import LLMError, _normalize_answer, chat as llm_chat, stream_chat as llm_stream_chat
from ..ratelimit import SlidingWindowLimiter
from ..reranking import RerankUnavailable, reranker
from ..schemas import MAX_SQLITE_ID, FeedbackBody, QueryBody

router = APIRouter()

query_limiter = SlidingWindowLimiter(limit=settings.queries_per_minute, window_seconds=60.0)

EMPTY_KB_ANSWER = "知识库当前为空：还没有任何可检索的文档。请联系管理员上传文档后再提问。"
HISTORY_TURNS = 3
HISTORY_CHAR_LIMIT = 2000


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _excerpt(text: str, limit: int = 300) -> str:
    one = " ".join((text or "").split())
    return one[:limit] + ("…" if len(one) > limit else "")


def _stored_document_ids(raw: str | None) -> list[int]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError("对话的限定文档范围数据损坏") from exc
    if not isinstance(values, list) or any(
        type(value) is not int or not 0 < value <= MAX_SQLITE_ID for value in values
    ):
        raise ValueError("对话的限定文档范围数据损坏")
    return sorted(set(values))


def _scope_mode_of(document_ids: list[int] | None) -> str:
    """把文档范围列表映射成对外可见的显式模式：空 = 全库，非空 = 仅选中文档。"""
    return "selected" if document_ids else "all"


def _require_ready_documents(db: sqlite3.Connection, document_ids: list[int], user) -> None:
    if not document_ids:
        return
    # 先判可见性：不可见的文档一律按"不存在"处理（404），
    # 否则可以通过限定一个受限文档来探测它是否存在。
    allowed = acl.visible_document_ids(db, user)
    if allowed is not None and not set(document_ids) <= allowed:
        raise HTTPException(status_code=404, detail="限定文档不存在")
    placeholders = ",".join("?" * len(document_ids))
    rows = db.execute(
        f"SELECT id FROM documents WHERE status='ready' AND id IN ({placeholders})",
        document_ids,
    ).fetchall()
    if {int(row["id"]) for row in rows} != set(document_ids):
        raise HTTPException(
            status_code=400,
            detail="所选文档已删除或尚未处理完成，请刷新文档列表后重新选择；不会回退到全库检索",
        )


def _is_continuation(current: dict, following: dict) -> bool:
    """同一位置的下一切片只有在保留真实重叠时才算续段。"""
    if current["document_id"] != following["document_id"] \
            or following.get("seq") != current.get("seq", -2) + 1:
        return False
    same_location = current.get("page") is not None and current.get("page") == following.get("page") \
        or current.get("paragraph") is not None \
        and current.get("paragraph") == following.get("paragraph")
    if not same_location:
        return False
    left = (current.get("content") or "").rstrip()
    right = (following.get("content") or "").lstrip()
    if len(right) < 24:
        return False
    start = left.rfind(right[:24])
    overlap = left[start:] if start >= 0 else ""
    return len(overlap) >= 24 and right.startswith(overlap) and len(right) > len(overlap)


def _ranked_sources(sources: list[dict], k: int, trace: dict | None = None) -> list[dict]:
    """重排后的相关性顺序不再被旧启发式覆盖；只去同来源完全重复文本。"""
    candidates, seen, reasons = [], set(), {}
    for source in sources:
        if not source["content"].strip():
            reasons[source["chunk_id"]] = "empty_content"
            continue
        key = (source["document_id"], source.get("page"), source.get("paragraph"), source["content"])
        if key in seen:
            reasons[source["chunk_id"]] = "exact_same_source_text"
        else:
            seen.add(key)
            candidates.append(source)
    following = {(s["document_id"], s.get("seq")): s for s in candidates if isinstance(s.get("seq"), int)}
    selected, ids = [], set()
    for source in candidates:
        if len(selected) >= k:
            break
        if source["chunk_id"] in ids:
            continue
        selected.append(source)
        ids.add(source["chunk_id"])
        reasons[source["chunk_id"]] = "rerank_top_k"
        next_source = following.get((source["document_id"], source.get("seq", -2) + 1))
        if len(selected) < k and next_source and next_source["chunk_id"] not in ids and _is_continuation(source, next_source):
            selected.append(next_source)
            ids.add(next_source["chunk_id"])
            reasons[next_source["chunk_id"]] = "verified_continuation"
    if trace is not None:
        trace["selection"] = [{"chunk_id": s["chunk_id"], "selected": s["chunk_id"] in ids,
                               "reason": reasons.get(s["chunk_id"], "rank_budget")} for s in sources]
    return selected


def _select_sources(sources: list[dict], k: int, question: str = "", *, preserve_ranking: bool = False,
                    trace: dict | None = None) -> list[dict]:
    """同文档的包含型重复只保留完整片段，保留不同数值/版本的原始引用。"""
    if k <= 0:
        return []
    if preserve_ranking:
        return _ranked_sources(sources, k, trace)
    kept: list[tuple[dict, str]] = []
    for src in sorted(sources, key=lambda s: len(s["content"]), reverse=True):
        text = " ".join(src["content"].split())
        if text and not any(
            src["document_id"] == old["document_id"] and text in old_text
            for old, old_text in kept
        ):
            kept.append((src, text))
    ids = {src["chunk_id"] for src, _ in kept}
    candidates = [src for src in sources if src["chunk_id"] in ids]
    terms = set(technical_terms(question))
    versions = {term for term in terms if re.fullmatch(r"v\d+(?:[._-]\d+)*", term)}
    models = {term for term in terms if any(c.isdigit() for c in term)} - versions
    titles = {src["document_id"]: set(technical_terms(Path(src["filename"]).stem))
              | set(technical_terms(Path(src["filename"]).stem.replace("_", " "))) for src in candidates}
    protocols = set(re.findall(r"(?<![a-z0-9])(?:ascii|hex|secs)(?![a-z0-9])", question.lower()))
    title_protocols = {src["document_id"]: set(re.findall(
        r"(?<![a-z0-9])(?:ascii|hex|secs)(?![a-z0-9])", Path(src["filename"]).stem.lower(),
    )) for src in candidates}
    context_titles = {doc: set(title) for doc, title in titles.items()}
    for src in candidates:
        for title in re.findall(r"(?im)^DOCUMENT TITLE:[ \t]*(?:\r?\n)?([^\r\n]+)", src["content"]):
            context_titles[src["document_id"]].update(technical_terms(title))
    strong_evidence_docs = set()
    bigrams = {
        segment[i:i + 2]
        for segment in re.findall(r"[\u4e00-\u9fff]+", question)
        for i in range(len(segment) - 1)
    }
    if bigrams:
        def lexical_score(src: dict) -> int:
            content = re.sub(r"\s+", "", src["content"])
            if "工作表《" in content and Path(src["filename"]).suffix.lower() != ".xlsx":
                return 0
            return sum(term in content for term in bigrams)

        for document_id in dict.fromkeys(src["document_id"] for src in candidates):
            positions = [i for i, src in enumerate(candidates) if src["document_id"] == document_id]
            scores = [(lexical_score(candidates[i]), i) for i in positions]
            ranked = sorted(scores, reverse=True)
            if ranked[0][0] >= 3 and (len(ranked) == 1 or ranked[0][0] >= ranked[1][0] + 2):
                first, best = positions[0], ranked[0][1]
                exact = terms - context_titles[document_id]
                retained = exact & {term for i in positions for term in technical_terms(candidates[i]["content"])}
                if not retained <= set(technical_terms(candidates[best]["content"])):
                    continue
                candidates[first], candidates[best] = candidates[best], candidates[first]
                strong_evidence_docs.add(document_id)
    matched_docs = {doc_id for doc_id, title in titles.items() if models & title}
    # 只有一个候选文档匹配型号、且技术词均在标题中时，不用通用标题词的词法名次排其正文。
    semantic_doc = next(iter(matched_docs)) if len(matched_docs) == 1 else None
    semantic_only = semantic_doc is not None and terms <= titles[semantic_doc] \
        and semantic_doc not in strong_evidence_docs
    channels = re.findall(r"通道\s*(\d+)(?![0-9A-Za-z])(?!\s*(?:[.\-~–—]|至|到))", question)
    compact_question = re.sub(r"\s+", "", question).lower()

    def priority(src: dict) -> tuple:
        content = src["content"]
        channel_match = bool(channels) and all(
            re.search(r"通道\s*" + re.escape(number)
                      + r"(?![0-9A-Za-z])(?!\s*(?:[.\-~–—]|至|到))", content) for number in channels
        )
        # 必须匹配完整单元格，包括多行内容；不能把多行测试项的首行当成另一种操作。
        cells = re.findall(r"(?:^|[；：\n])[A-Z]+\d+=(.*?)(?=；[A-Z]+\d+=|\n工作表《|$)", content, re.S)
        cell_match = Path(src["filename"]).suffix.lower() == ".xlsx" and any(
            4 <= len(value) <= 80 and re.search(r"[A-Za-z]", value) and re.search(r"[\u4e00-\u9fff]", value)
            and re.sub(r"\s+", "", value).lower() in compact_question for value in cells
        )
        # 身份只取实际文件名，不让正文顺带提及另一设备/协议冒充匹配。
        return (len(models & titles[src["document_id"]]),
                len(protocols & title_protocols[src["document_id"]]),
                len(versions & titles[src["document_id"]]), channel_match, cell_match,
                src["score"] if semantic_only and src["document_id"] == semantic_doc else 0)

    # 仅在已通过权限筛选的有界检索候选中稳定排序；同文档词法顺序不改变跨文档槽位。
    ordered = sorted(candidates, key=priority, reverse=True)
    by_sequence = {
        (src["document_id"], src.get("seq")): src for src in candidates
        if isinstance(src.get("seq"), int)
    }
    selected = []
    selected_ids = set()
    for src in ordered:
        if src["chunk_id"] in selected_ids:
            continue
        selected.append(src)
        selected_ids.add(src["chunk_id"])
        if len(selected) == k:
            break
        following = by_sequence.get((src["document_id"], src.get("seq", -2) + 1))
        if following and following["chunk_id"] not in selected_ids \
                and _is_continuation(src, following):
            selected.append(following)
            selected_ids.add(following["chunk_id"])
            if len(selected) == k:
                break
    return selected


def _sources_for_chat(
    db: sqlite3.Connection,
    chat_id: int,
    allowed: set[int] | None = None,
) -> tuple[list[dict], bool]:
    """返回 (当前可见的来源, 是否存在被隐藏的来源)。

    第二个返回值用来判定"该轮是否引用了现在已不可见的文档"。仅过滤来源是不够的：
    答案正文里可能逐字引用了原文，所以调用方需要据此整轮隐藏。
    """
    rows = db.execute(
        "SELECT s.chunk_id, s.document_id, s.score, s.page, s.paragraph, s.excerpt, d.filename "
        "FROM chat_sources s JOIN documents d ON d.id = s.document_id "
        "WHERE s.chat_id=? ORDER BY s.id",
        (chat_id,),
    ).fetchall()
    sources: list[dict] = []
    hidden = False
    for r in rows:
        # 读取历史时按**当前**权限重新判定，而不是沿用当时的授权。
        # 否则被撤销权限后，旧对话会继续把撤权资料交出去。
        if allowed is not None and int(r["document_id"]) not in allowed:
            hidden = True
            continue
        sources.append(
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "filename": r["filename"],
                "score": r["score"],
                "page": r["page"],
                "paragraph": r["paragraph"],
                "excerpt": r["excerpt"],
            }
        )
    return sources, hidden


def _feedback_for_chat(db: sqlite3.Connection, chat_id: int, user_id: int) -> dict | None:
    row = db.execute(
        "SELECT rating, comment, created_at FROM feedback WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def _conversation_history(
    db: sqlite3.Connection,
    conversation_id: int,
    allowed: set[int] | None,
) -> list[dict]:
    rows = db.execute(
        "SELECT id, question, answer FROM chats WHERE conversation_id=? AND status='ok' "
        "ORDER BY turn_index DESC, id DESC LIMIT ?",
        (conversation_id, HISTORY_TURNS),
    ).fetchall()
    kept: list[dict] = []
    remaining = HISTORY_CHAR_LIMIT
    for row in rows:
        # 历史答案可能逐字包含后来被撤权的文档内容，不能继续送入模型。
        _, hidden = _sources_for_chat(db, int(row["id"]), allowed)
        if hidden:
            continue
        question = (row["question"] or "").strip()
        answer = (row["answer"] or "").strip()
        if remaining <= 0:
            break
        question = question[:remaining]
        remaining -= len(question)
        answer = answer[:remaining]
        remaining -= len(answer)
        kept.append({"question": question, "answer": answer})
    kept.reverse()
    return kept


@router.get("/documents")
def list_query_documents(
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    rows = db.execute(
        "SELECT id, filename, version FROM documents WHERE status='ready' ORDER BY filename, id"
    ).fetchall()
    # 受限文档不出现在可选项里：否则等于告诉用户"存在一份你看不到的资料"。
    allowed = acl.visible_document_ids(db, user)
    items = [
        dict(row) for row in rows
        if allowed is None or int(row["id"]) in allowed
    ]
    return {"items": items, "total": len(items)}


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def open_document(
    document_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    row = db.execute(
        "SELECT id, filename, stored_name, content_type FROM documents "
        "WHERE id=? AND status='ready'",
        (document_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    upload_root = settings.upload_dir.resolve()
    path = (upload_root / row["stored_name"]).resolve()
    if path.parent != upload_root or not path.is_file():
        raise HTTPException(status_code=404, detail="文档文件不存在")

    audit.log_audit(
        db,
        action="document_open",
        user_id=user.id,
        username=user.username,
        # 管理员可以打开受限原文（否则无法管理这些文档），但必须留下可核查的例外记录。
        detail=f"doc:{document_id} 文件:{row['filename']}"
        + (" [受限文档·管理员例外]" if acl.is_restricted(db, document_id) else ""),
        ip=_ip(request),
    )
    return FileResponse(
        path,
        media_type=row["content_type"] or "application/octet-stream",
        filename=row["filename"],
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def _store_chat(
    db: sqlite3.Connection,
    user_id: int,
    question: str,
    answer: str,
    status: str,
    error: str | None,
    *,
    model: str | None = None,
    latency_ms: int | None = None,
    retrieval_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    sources: list[dict] | None = None,
    conversation_id: int | None = None,
    document_ids: list[int] | None = None,
) -> tuple[int, int]:
    created = now_iso()
    if conversation_id is None:
        cur = db.execute(
            "INSERT INTO conversations (user_id, title, document_ids, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, question[:30], json.dumps(document_ids or []), created, created),
        )
        conversation_id = int(cur.lastrowid)
    turn_index = db.execute(
        "SELECT COALESCE(MAX(turn_index),0)+1 AS n FROM chats WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()["n"]
    cur = db.execute(
        "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, error,"
        " model, prompt_tokens, completion_tokens, latency_ms, retrieval_ms, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            user_id,
            conversation_id,
            turn_index,
            question,
            answer,
            status,
            error,
            model,
            prompt_tokens,
            completion_tokens,
            latency_ms,
            retrieval_ms,
            created,
        ),
    )
    chat_id = int(cur.lastrowid)
    db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (created, conversation_id))
    for s in sources or []:
        db.execute(
            "INSERT INTO chat_sources (chat_id, document_id, chunk_id, score, page, paragraph, excerpt)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                chat_id,
                s["document_id"],
                s.get("chunk_id"),
                s["score"],
                s.get("page"),
                s.get("paragraph"),
                _excerpt(s.get("content") or ""),
            ),
        )
    return chat_id, conversation_id


def retrieve_sources(
    db: sqlite3.Connection,
    question: str,
    document_ids: set[int] | None,
    top_k: int,
    *,
    history: list[dict] | None = None,
) -> tuple[list[dict], int, str | None]:
    """检索并选出引用来源（Web 问答与 MCP 服务共用这一条路径）。

    `document_ids` 的语义与 `vector_index.search` 一致：`None` 表示不限定，
    空集合表示"没有任何可见文档"（会返回空结果而不是退化成全库）。

    返回 `(sources, retrieval_ms, empty_reason)`；`empty_reason` 为
    `"no_hits"`（向量/词法都没有命中）或 `"no_selected"`（命中但被选段阶段全部丢弃），
    以便调用方沿用各自的无结果文案。`EmbeddingUnavailable` / `RerankUnavailable` /
    `ValueError` 原样抛给调用方映射成各自协议的 503/500。
    """
    retrieval_started = time.monotonic()
    retrieval_question, auxiliary_question = semantic_queries(
        question, history, settings.history_auxiliary
    )
    qvec = embedding_service.embed_query(retrieval_question)
    auxiliary_options = (
        {"auxiliary_vec": embedding_service.embed_query(auxiliary_question)}
        if auxiliary_question else {}
    )
    min_score = settings.min_relevance_score if settings.embed_backend != "mock" else None
    # 旧索引可能包含大量重叠短尾片段；有界扩大候选，去重后仍只发送 Top-K。
    hits = vector_index.search(
        qvec,
        min(200, top_k * 20),
        document_ids=document_ids,
        min_score=min_score,
        query_text=question,
        **auxiliary_options,
    )
    if not hits:
        return [], int((time.monotonic() - retrieval_started) * 1000), "no_hits"
    ids = [hit["chunk_id"] for hit in hits]
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph, c.content, d.filename "
        f"FROM chunks c JOIN documents d ON d.id = c.document_id "
        f"WHERE c.id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["chunk_id"]: row for row in rows}
    sources: list[dict] = []
    for hit in hits:
        row = by_id.get(hit["chunk_id"])
        if row is None:
            continue
        sources.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "document_id": int(row["document_id"]),
                "seq": int(row["seq"]),
                "filename": row["filename"],
                "page": row["page"],
                "paragraph": row["paragraph"],
                "score": round(float(hit["score"]), 4),
                "content": row["content"],
            }
        )
    if settings.rerank_enabled:
        sources = reranker.rank(question, sources)
    sources = _select_sources(
        sources, top_k, question, preserve_ranking=settings.rerank_enabled
    )
    for source in sources:
        if Path(source["filename"]).suffix.lower() == ".xlsx":
            source["table_headers"] = vector_index.table_headers_for(source["document_id"])
    retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)
    if not sources:
        return [], retrieval_ms, "no_selected"
    return sources, retrieval_ms, None


@router.post("/query")
def query(
    body: QueryBody,
    request: Request,
    stream: bool = False,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    question = body.question.strip()
    ip = _ip(request)
    conversation_id = body.conversation_id
    requested_scope_mode = body.scope_mode
    document_ids = body.document_ids or []
    history: list[dict] = []
    if conversation_id is not None:
        owned = db.execute(
            "SELECT id, document_ids FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user.id),
        ).fetchone()
        if owned is None:
            raise HTTPException(status_code=404, detail="对话不存在")
        try:
            stored_ids = _stored_document_ids(owned["document_ids"])
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        stored_mode = _scope_mode_of(stored_ids)
        # 范围在首问时固化。旧客户端省略 scope_mode 仍可追问（兼容），
        # 但一旦显式传了模式或文档列表，就必须与固化范围完全一致。
        if body.document_ids is not None and body.document_ids != stored_ids:
            raise HTTPException(status_code=409, detail="已有对话不能改变限定文档范围，请新建对话")
        if requested_scope_mode is not None and requested_scope_mode != stored_mode:
            raise HTTPException(
                status_code=409,
                detail="已有对话的检索范围已固定（全库检索 / 仅选中文档），不能改变，请新建对话",
            )
        document_ids = stored_ids
    elif requested_scope_mode == "all":
        # 显式全库：非空 document_ids 已在 QueryBody 层被拒绝，这里不再保留列表。
        document_ids = []
    elif requested_scope_mode == "selected":
        # QueryBody 已保证非空；再拦一次，避免将来放宽校验时悄悄回退到全库。
        if not document_ids:
            raise HTTPException(
                status_code=422,
                detail="仅选中文档模式必须至少选择一个文档，不能回退到全库检索",
            )
    else:
        # 兼容旧客户端：不传 scope_mode 时，空列表表示全库，非空表示限定。
        document_ids = body.document_ids or []
    _require_ready_documents(db, document_ids, user)
    # 检索范围 = 用户请求的范围 ∩ 他实际可见的范围。
    # 注意 set() 与 None 语义不同：None 表示不限定，空集合表示"没有任何可见文档"，
    # 后者会让检索直接返回空（进而拒答），而不是退化成全库检索。
    scope = acl.effective_scope(db, user, document_ids)
    if conversation_id is not None:
        history = _conversation_history(db, conversation_id, acl.visible_document_ids(db, user))

    rt_values = rt.get_all(db)
    ok, retry = query_limiter.allow(
        key=f"user:{user.id}", limit=rt_values["queries_per_minute"]
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=(
                f"问答频率超限（每分钟 {rt_values['queries_per_minute']} 次），"
                f"请约 {int(retry) + 1} 秒后再试"
            ),
        )

    if embedding_service.state != "ready":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "embed_not_ready",
                "message": embedding_service.message or "嵌入模型尚未就绪，请稍后再试",
            },
        )

    # 检索段耗时。在 refuse 之前初始化：知识库为空/无权访问这类拒答发生在检索之前，
    # 此时它确实是 None，而不是"未测量"。
    retrieval_ms: int | None = None

    def refuse(message: str, action: str) -> dict:
        chat_id, stored_conversation_id = _store_chat(
            db, user.id, question, message, "ok", None,
            model=None, retrieval_ms=retrieval_ms,
            conversation_id=conversation_id, document_ids=document_ids,
        )
        audit.log_audit(
            db,
            action=action,
            user_id=user.id,
            username=user.username,
            detail=question[:200],
            ip=ip,
        )
        return {
            "answer": message,
            "chat_id": chat_id,
            "conversation_id": stored_conversation_id,
            "sources": [],
            "status": "ok",
            "retrieval_ms": retrieval_ms,
            "scope_mode": _scope_mode_of(document_ids),
            "document_ids": list(document_ids),
        }

    if vector_index.size() == 0:
        return refuse(EMPTY_KB_ANSWER, "query_refused_empty")

    if scope == set():
        # 该账号当前没有任何可见文档。明确说明原因，而不是含糊地回一句"未找到"。
        return refuse(
            "你当前没有可访问的文档。请联系管理员为本账号开通权限。",
            "query_refused_no_access",
        )

    try:
        # 检索段计时由 retrieve_sources 内部记录；这里统一映射到 HTTP 语义。
        sources, retrieval_ms, empty_reason = retrieve_sources(
            db, question, scope, rt_values["top_k"], history=history
        )
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "embed_not_ready", "message": str(exc)}) from exc
    except RerankUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "rerank_unavailable", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if empty_reason == "no_hits":
        return refuse("未找到与问题相关的可用文档，请换个问法或联系管理员。", "query_no_match")
    if empty_reason is not None:
        return refuse("知识库没有可用的检索结果，请稍后重试或联系管理员。", "query_no_match")

    doc_names = sorted({s["filename"] for s in sources})

    if stream:
        def events():
            def line(data: dict) -> str:
                return json.dumps(data, ensure_ascii=False) + "\n"

            if not llm_gate.acquire(90.0):
                yield line({"type": "error", "message": "系统繁忙：并发模型请求已达上限，请稍后重试"})
                return
            answer_parts: list[str] = []
            usage: dict = {}
            try:
                for event in llm_stream_chat(question, sources, history):
                    if event["type"] == "delta":
                        answer_parts.append(event["text"])
                        yield line(event)
                    else:
                        usage = event
            except LLMError as exc:
                with closing(connect()) as stream_db:
                    chat_id, stored_conversation_id = _store_chat(
                        stream_db, user.id, question, "", "error", f"{exc.code}: {exc.message}",
                        model=settings.deepseek_model, retrieval_ms=retrieval_ms,
                        conversation_id=conversation_id, document_ids=document_ids,
                    )
                    audit.log_audit(
                        stream_db, action="llm_query_failed", user_id=user.id, username=user.username,
                        detail=json.dumps({"question": question[:200], "documents": doc_names,
                                           "code": exc.code, "chat_id": chat_id}, ensure_ascii=False), ip=ip,
                    )
                yield line({"type": "error", "code": exc.code, "message": exc.message,
                            "conversation_id": stored_conversation_id})
                return
            finally:
                llm_gate.release()

            answer = _normalize_answer("".join(answer_parts).strip())
            with closing(connect()) as stream_db:
                chat_id, stored_conversation_id = _store_chat(
                    stream_db, user.id, question, answer, "ok", None,
                    model=usage.get("model", settings.deepseek_model), latency_ms=usage["latency_ms"],
                    retrieval_ms=retrieval_ms,
                    prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
                    sources=sources, conversation_id=conversation_id, document_ids=document_ids,
                )
                audit.log_audit(
                    stream_db, action="llm_query", user_id=user.id, username=user.username,
                    detail=json.dumps({"question": question[:200], "documents": doc_names,
                                       "model": usage.get("model", settings.deepseek_model),
                                       "latency_ms": usage["latency_ms"],
                                       "retrieval_ms": retrieval_ms,
                                       "prompt_tokens": usage["prompt_tokens"],
                                       "completion_tokens": usage["completion_tokens"]}, ensure_ascii=False), ip=ip,
                )
            yield line({"type": "done", "chat_id": chat_id, "conversation_id": stored_conversation_id,
                        "latency_ms": usage["latency_ms"], "retrieval_ms": retrieval_ms,
                        "scope_mode": _scope_mode_of(document_ids),
                        "document_ids": list(document_ids)})

        return StreamingResponse(events(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    if not llm_gate.acquire(90.0):
        raise HTTPException(
            status_code=503,
            detail="系统繁忙：并发模型请求已达上限，请稍后重试",
        )
    try:
        try:
            result = llm_chat(question, sources, history)
        except LLMError as exc:
            chat_id, stored_conversation_id = _store_chat(
                db, user.id, question, "", "error", f"{exc.code}: {exc.message}",
                model=settings.deepseek_model, retrieval_ms=retrieval_ms,
                conversation_id=conversation_id,
                document_ids=document_ids,
            )
            audit.log_audit(
                db,
                action="llm_query_failed",
                user_id=user.id,
                username=user.username,
                detail=json.dumps(
                    {
                        "question": question[:200],
                        "documents": doc_names,
                        "code": exc.code,
                        "chat_id": chat_id,
                    },
                    ensure_ascii=False,
                ),
                ip=ip,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "chat_id": chat_id,
                    "conversation_id": stored_conversation_id,
                },
            ) from exc
    finally:
        llm_gate.release()

    chat_id, stored_conversation_id = _store_chat(
        db,
        user.id,
        question,
        result["answer"],
        "ok",
        None,
        model=result["model"],
        latency_ms=result["latency_ms"],
        retrieval_ms=retrieval_ms,
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        sources=sources,
        conversation_id=conversation_id,
        document_ids=document_ids,
    )
    audit.log_audit(
        db,
        action="llm_query",
        user_id=user.id,
        username=user.username,
        detail=json.dumps(
            {
                "question": question[:200],
                "documents": doc_names,
                "model": result["model"],
                "latency_ms": result["latency_ms"],
                "retrieval_ms": retrieval_ms,
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
            },
            ensure_ascii=False,
        ),
        ip=ip,
    )

    return {
        "answer": result["answer"],
        "chat_id": chat_id,
        "conversation_id": stored_conversation_id,
        "sources": [
            {k: s[k] for k in ("chunk_id", "document_id", "filename", "page", "paragraph", "score")}
            for s in sources
        ],
        "status": "ok",
        # 分阶段耗时：便于前端/巡检直接看到瓶颈在哪一段，而不必去数据库里翻。
        "latency_ms": result["latency_ms"],
        "retrieval_ms": retrieval_ms,
        # 回显实际生效的范围：前端据此显示"全库检索 / 仅选中文档"，而不是自行猜测。
        "scope_mode": _scope_mode_of(document_ids),
        "document_ids": list(document_ids),
    }


@router.get("/conversations")
def list_conversations(
    limit: int = 25,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total = db.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE user_id=?", (user.id,)
    ).fetchone()["n"]
    rows = db.execute(
        "SELECT v.id, v.title, v.created_at, v.updated_at, COUNT(c.id) AS turn_count, "
        "COALESCE(MAX(CASE WHEN c.status='error' THEN 1 ELSE 0 END),0) AS has_error "
        "FROM conversations v LEFT JOIN chats c ON c.conversation_id=v.id "
        "WHERE v.user_id=? GROUP BY v.id ORDER BY v.updated_at DESC, v.id DESC LIMIT ? OFFSET ?",
        (user.id, limit, offset),
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    conversation = db.execute(
        "SELECT v.*, u.username AS owner_username FROM conversations v "
        "JOIN users u ON u.id=v.user_id WHERE v.id=?",
        (conversation_id,),
    ).fetchone()
    if conversation is None or (user.role != "root" and conversation["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="对话不存在")
    turns = db.execute(
        "SELECT * FROM chats WHERE conversation_id=? ORDER BY turn_index, id",
        (conversation_id,),
    ).fetchall()
    item = dict(conversation)
    item["username"] = item.pop("owner_username")
    try:
        item["document_ids"] = _stored_document_ids(item.get("document_ids"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # 先按存储的真实范围判定模式，再做可见性过滤：即便部分文档已不可见，
    # 也不能把"仅选中文档"的对话显示成"全库检索"。
    item["scope_mode"] = _scope_mode_of(item["document_ids"])
    allowed = acl.visible_document_ids(db, user)
    if allowed is not None:
        # 不把已不可见的文档 id 回显给用户，避免暴露"存在一份你看不到的资料"。
        item["document_ids"] = [d for d in item["document_ids"] if d in allowed]
    item["turns"] = []
    for turn in turns:
        record = dict(turn)
        sources, hidden = _sources_for_chat(db, turn["id"], allowed)
        if hidden:
            # 该轮引用的文档已不再对该账号可见。答案正文里可能逐字引用了原文，
            # 因此整轮隐藏，而不是只删掉引用来源。
            record["answer"] = "（该轮引用的文档已不再对你可见，内容已隐藏）"
            record["redacted"] = True
        record["sources"] = sources
        record["feedback"] = _feedback_for_chat(db, turn["id"], user.id)
        item["turns"].append(record)
    return item


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    conversation = db.execute(
        "SELECT v.id, v.user_id, v.title, u.username AS owner_username FROM conversations v "
        "JOIN users u ON u.id=v.user_id WHERE v.id=?",
        (conversation_id,),
    ).fetchone()
    if conversation is None or (user.role != "root" and conversation["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="对话不存在")
    db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
    audit.log_audit(
        db,
        action="conversation_delete",
        user_id=user.id,
        username=user.username,
        detail=f"conversation:{conversation_id} 所有者:{conversation['owner_username']} 标题:{conversation['title']}",
        ip=_ip(request),
    )


@router.get("/chats")
def list_chats(
    limit: int = 25,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total = db.execute(
        "SELECT COUNT(*) AS n FROM chats WHERE user_id=?", (user.id,)
    ).fetchone()["n"]
    rows = db.execute(
        "SELECT id, question, answer, status, error, model, latency_ms, retrieval_ms, created_at "
        "FROM chats WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user.id, limit, offset),
    ).fetchall()
    allowed = acl.visible_document_ids(db, user)
    items = []
    for row in rows:
        item = dict(row)
        _, hidden = _sources_for_chat(db, int(row["id"]), allowed)
        if hidden:
            item["answer"] = "（该问答引用的文档已不再对你可见，内容已隐藏）"
            item["redacted"] = True
        items.append(item)
    return {"items": items, "total": total}


@router.get("/chats/{chat_id}")
def get_chat(
    chat_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    chat = db.execute(
        "SELECT c.*, u.username AS owner_username FROM chats c "
        "JOIN users u ON u.id = c.user_id WHERE c.id=?",
        (chat_id,),
    ).fetchone()
    if chat is None or (user.role != "root" and chat["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="问答记录不存在")
    item = dict(chat)
    item["username"] = item.pop("owner_username")
    allowed = acl.visible_document_ids(db, user)
    sources, hidden = _sources_for_chat(db, chat_id, allowed)
    if hidden:
        # 与对话详情一致：撤权后连单条问答也不能继续交出内容。
        item["answer"] = "（该问答引用的文档已不再对你可见，内容已隐藏）"
        item["redacted"] = True
    item["sources"] = sources
    item["feedback"] = _feedback_for_chat(db, chat_id, user.id)
    return item


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(
    chat_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    chat = db.execute(
        "SELECT c.id, c.user_id, c.conversation_id, u.username AS owner_username FROM chats c "
        "JOIN users u ON u.id = c.user_id WHERE c.id=?",
        (chat_id,),
    ).fetchone()
    if chat is None or (user.role != "root" and chat["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="问答记录不存在")

    db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    latest = db.execute(
        "SELECT MAX(created_at) AS updated_at FROM chats WHERE conversation_id=?",
        (chat["conversation_id"],),
    ).fetchone()["updated_at"]
    if latest is None:
        db.execute("DELETE FROM conversations WHERE id=?", (chat["conversation_id"],))
    else:
        db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (latest, chat["conversation_id"]),
        )
    audit.log_audit(
        db,
        action="chat_delete",
        user_id=user.id,
        username=user.username,
        detail=f"chat:{chat_id} 所有者:{chat['owner_username']}",
        ip=_ip(request),
    )


@router.post("/chats/{chat_id}/feedback")
def save_feedback(
    chat_id: int,
    body: FeedbackBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    chat = db.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, user.id)).fetchone()
    if chat is None:
        raise HTTPException(status_code=404, detail="问答记录不存在")
    now = now_iso()
    db.execute(
        "INSERT INTO feedback (chat_id, user_id, rating, comment, created_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET rating=excluded.rating, "
        "comment=excluded.comment, created_at=excluded.created_at",
        (chat_id, user.id, body.rating, body.comment, now),
    )
    audit.log_audit(
        db,
        action="feedback_submit",
        user_id=user.id,
        username=user.username,
        detail=f"chat:{chat_id} rating:{body.rating}",
        ip=_ip(request),
    )
    return {"chat_id": chat_id, "rating": body.rating, "comment": body.comment, "created_at": now}
