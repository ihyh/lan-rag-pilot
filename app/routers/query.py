"""问答（检索 + 模型调用）与个人问答历史。"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import audit, runtime as rt
from ..config import settings
from ..db import get_db, now_iso
from ..deps import require_user
from ..embeddings import EmbeddingUnavailable, embedding_service
from ..gate import llm_gate
from ..index import vector_index
from ..llm import LLMError, chat as llm_chat
from ..ratelimit import SlidingWindowLimiter
from ..schemas import FeedbackBody, QueryBody

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


def _sources_for_chat(db: sqlite3.Connection, chat_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT s.chunk_id, s.document_id, s.score, s.page, s.paragraph, s.excerpt, d.filename "
        "FROM chat_sources s JOIN documents d ON d.id = s.document_id "
        "WHERE s.chat_id=? ORDER BY s.id",
        (chat_id,),
    ).fetchall()
    return [
        {
            "chunk_id": r["chunk_id"],
            "document_id": r["document_id"],
            "filename": r["filename"],
            "score": r["score"],
            "page": r["page"],
            "paragraph": r["paragraph"],
            "excerpt": r["excerpt"],
        }
        for r in rows
    ]


def _feedback_for_chat(db: sqlite3.Connection, chat_id: int, user_id: int) -> dict | None:
    row = db.execute(
        "SELECT rating, comment, created_at FROM feedback WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def _conversation_history(db: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT question, answer FROM chats WHERE conversation_id=? AND status='ok' "
        "ORDER BY turn_index DESC, id DESC LIMIT ?",
        (conversation_id, HISTORY_TURNS),
    ).fetchall()
    kept: list[dict] = []
    remaining = HISTORY_CHAR_LIMIT
    for row in rows:
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


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def open_document(
    document_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
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
        detail=f"doc:{document_id} 文件:{row['filename']}",
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
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    sources: list[dict] | None = None,
    conversation_id: int | None = None,
) -> tuple[int, int]:
    created = now_iso()
    if conversation_id is None:
        cur = db.execute(
            "INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (user_id, question[:30], created, created),
        )
        conversation_id = int(cur.lastrowid)
    turn_index = db.execute(
        "SELECT COALESCE(MAX(turn_index),0)+1 AS n FROM chats WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()["n"]
    cur = db.execute(
        "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, error,"
        " model, prompt_tokens, completion_tokens, latency_ms, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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


@router.post("/query")
def query(
    body: QueryBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    question = body.question.strip()
    ip = _ip(request)
    conversation_id = body.conversation_id
    history: list[dict] = []
    if conversation_id is not None:
        owned = db.execute(
            "SELECT id FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user.id),
        ).fetchone()
        if owned is None:
            raise HTTPException(status_code=404, detail="对话不存在")
        history = _conversation_history(db, conversation_id)

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

    def refuse(message: str, action: str) -> dict:
        chat_id, stored_conversation_id = _store_chat(
            db, user.id, question, message, "ok", None,
            model=None, conversation_id=conversation_id,
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
        }

    if vector_index.size() == 0:
        return refuse(EMPTY_KB_ANSWER, "query_refused_empty")

    try:
        retrieval_question = f"{history[-1]['question']}\n{question}" if history else question
        qvec = embedding_service.embed_query(retrieval_question)
        min_score = settings.min_relevance_score if settings.embed_backend != "mock" else None
        hits = vector_index.search(qvec, rt_values["top_k"], min_score=min_score)
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "embed_not_ready", "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not hits:
        return refuse("未找到与问题相关的可用文档，请换个问法或联系管理员。", "query_no_match")

    ids = [h["chunk_id"] for h in hits]
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT c.id AS chunk_id, c.document_id, c.page, c.paragraph, c.content, d.filename "
        f"FROM chunks c JOIN documents d ON d.id = c.document_id "
        f"WHERE c.id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r["chunk_id"]: r for r in rows}
    sources: list[dict] = []
    for h in hits:
        r = by_id.get(h["chunk_id"])
        if r is None:
            continue
        sources.append(
            {
                "chunk_id": int(r["chunk_id"]),
                "document_id": int(r["document_id"]),
                "filename": r["filename"],
                "page": r["page"],
                "paragraph": r["paragraph"],
                "score": round(float(h["score"]), 4),
                "content": r["content"],
            }
        )
    if not sources:
        return refuse("知识库没有可用的检索结果，请稍后重试或联系管理员。", "query_no_match")

    doc_names = sorted({s["filename"] for s in sources})

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
                model=settings.deepseek_model,
                conversation_id=conversation_id,
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
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        sources=sources,
        conversation_id=conversation_id,
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
    item["turns"] = []
    for turn in turns:
        record = dict(turn)
        record["sources"] = _sources_for_chat(db, turn["id"])
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
        "SELECT id, question, answer, status, error, model, latency_ms, created_at "
        "FROM chats WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user.id, limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


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
    item["sources"] = _sources_for_chat(db, chat_id)
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
