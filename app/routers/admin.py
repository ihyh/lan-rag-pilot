"""root 管理接口：文档 / 用户 / 审计 / 系统概览与运行参数。"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from .. import audit, ingest, runtime as rt
from ..config import settings
from ..db import get_db, now_iso
from ..deps import require_kb_admin, require_root, stored_role_fields
from ..embeddings import EmbeddingUnavailable, embedding_service
from ..gate import llm_gate
from ..ingest import IngestError
from ..schemas import (
    SettingsPatch,
    UserCreate,
    UserPatch,
)
from ..security import hash_password

router = APIRouter()


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _clean_filename(raw: str | None) -> str:
    if not raw:
        raise HTTPException(status_code=400, detail="缺少文件名")
    name = Path(raw.replace("\\", "/")).name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件名无效")
    return name


def _clean_version(raw: str | None) -> str:
    value = (raw or "1.0").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", value):
        raise HTTPException(status_code=422, detail="版本号须为 1-32 位字母、数字、点、下划线或连字符")
    return value


def _clean_date(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="日期须为 YYYY-MM-DD") from None
    return value


def _raise_ingest(exc: IngestError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _raise_embed(exc: EmbeddingUnavailable) -> None:
    raise HTTPException(
        status_code=503, detail={"code": "embed_not_ready", "message": str(exc)}
    ) from exc


def _require_manageable_document(db: sqlite3.Connection, doc_id: int) -> None:
    if db.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="文档不存在")


def _document_rows(db: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[dict]:
    rows = db.execute(
        f"SELECT d.*, u.username AS uploaded_by_name FROM documents d "
        f"LEFT JOIN users u ON u.id = d.uploaded_by "
        f"{where} ORDER BY d.id DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 文档管理 ----------------

@router.get("/admin/documents")
def list_documents(
    version: str | None = Query(default=None, max_length=32),
    uploaded_date_from: str | None = Query(default=None),
    uploaded_date_to: str | None = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    where: list[str] = []
    params: list[object] = []
    if version:
        where.append("d.version=?")
        params.append(_clean_version(version))
    date_from = _clean_date(uploaded_date_from)
    date_to = _clean_date(uploaded_date_to)
    if date_from:
        where.append("substr(d.created_at,1,10)>=?")
        params.append(date_from)
    if date_to:
        where.append("substr(d.created_at,1,10)<=?")
        params.append(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="上传日期起始值不能晚于结束值")
    clause = "WHERE " + " AND ".join(where) if where else ""
    items = _document_rows(db, clause, tuple(params))
    return {"items": items, "total": len(items)}


@router.post("/admin/documents", status_code=201)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(default="1.0"),
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    filename = _clean_filename(file.filename)
    version = _clean_version(version)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"文件超过 {settings.max_upload_mb} MB 上限"
        )
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"文件超过 {settings.max_upload_mb} MB 上限"
        )
    try:
        doc, kind = ingest.register_bytes(
            db,
            filename=filename,
            content_type=file.content_type or "",
            data=data,
            user_id=user.id,
            version=version,
        )
        doc = ingest.index_registered_document(db, doc["id"], kind)
    except IngestError as exc:
        audit.log_audit(
            db,
            action="doc_upload_failed",
            user_id=user.id,
            username=user.username,
            detail=f"文件:{filename} 原因:{exc.message}",
            ip=_ip(request),
        )
        _raise_ingest(exc)
    except EmbeddingUnavailable as exc:
        _raise_embed(exc)
    audit.log_audit(
        db,
        action="doc_upload",
        user_id=user.id,
        username=user.username,
        detail=f"doc:{doc['id']} 文件:{filename} 版本:{version} 上传日期:{doc['created_at'][:10]} 切片数:{doc['num_chunks']}",
        ip=_ip(request),
    )
    return doc


@router.delete("/admin/documents/{doc_id}", status_code=204)
def delete_document(
    doc_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    _require_manageable_document(db, doc_id)
    try:
        with ingest.ingest_lock:
            removed = ingest.delete_document(db, doc_id)
    except IngestError as exc:
        _raise_ingest(exc)
    audit.log_audit(
        db,
        action="doc_delete",
        user_id=user.id,
        username=user.username,
        detail=f"doc:{removed['id']} 文件:{removed['filename']}",
        ip=_ip(request),
    )
    return Response(status_code=204)


@router.post("/admin/documents/{doc_id}/reindex")
def reindex_document(
    doc_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    _require_manageable_document(db, doc_id)
    try:
        with ingest.ingest_lock:
            doc = ingest.reindex_document(db, doc_id)
    except IngestError as exc:
        audit.log_audit(
            db,
            action="doc_reindex_failed",
            user_id=user.id,
            username=user.username,
            detail=f"doc:{doc_id} 原因:{exc.message}",
            ip=_ip(request),
        )
        _raise_ingest(exc)
    except EmbeddingUnavailable as exc:
        _raise_embed(exc)
    audit.log_audit(
        db,
        action="doc_reindex",
        user_id=user.id,
        username=user.username,
        detail=f"doc:{doc_id} 文件:{doc['filename']} 切片数:{doc['num_chunks']}",
        ip=_ip(request),
    )
    return doc


# ---------------- 用户管理 ----------------

@router.get("/admin/users")
def list_users(db: sqlite3.Connection = Depends(get_db), _=Depends(require_root)):
    rows = db.execute(
        "SELECT u.id, u.username, CASE WHEN u.role='root' THEN 'root' "
        "WHEN u.is_kb_admin=1 THEN 'kb_admin' ELSE 'user' END AS role, "
        "u.is_active, u.last_login_at, u.created_at, u.updated_at "
        "FROM users u ORDER BY u.id"
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/admin/users", status_code=201)
def create_user(
    body: UserCreate,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    now = now_iso()
    db_role, is_kb_admin = stored_role_fields(body.role)
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, role, is_kb_admin, is_active, created_at, updated_at)"
            " VALUES (?,?,?,?,1,?,?)",
            (body.username, hash_password(body.password), db_role, is_kb_admin, now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="用户名已存在") from None
    audit.log_audit(
        db,
        action="user_create",
        user_id=user.id,
        username=user.username,
        detail=f"新用户:{body.username} 角色:{body.role}",
        ip=_ip(request),
    )
    return {
        "id": int(cur.lastrowid),
        "username": body.username,
        "role": body.role,
        "is_active": True,
        "created_at": now,
    }


@router.patch("/admin/users/{user_id}")
def update_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not any([body.password, body.role, body.is_active is not None]):
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    if user_id == user.id:
        if (body.role is not None and body.role != "root") or body.is_active is False:
            raise HTTPException(status_code=400, detail="不能停用或降级自己的账号")

    next_role = stored_role_fields(body.role)[0] if body.role else target["role"]
    next_active = target["is_active"] if body.is_active is None else int(body.is_active)
    if target["role"] == "root" and (next_role != "root" or not next_active):
        active_roots = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role='root' AND is_active=1"
        ).fetchone()["n"]
        if active_roots <= 1:
            raise HTTPException(status_code=400, detail="系统至少需要保留一个启用的 root 账号")

    updates: list[str] = []
    params: list = []
    if body.password:
        updates.append("password_hash=?")
        params.append(hash_password(body.password))
    if body.role:
        db_role, is_kb_admin = stored_role_fields(body.role)
        updates.extend(["role=?", "is_kb_admin=?"])
        params.extend([db_role, is_kb_admin])
    if body.is_active is not None:
        updates.append("is_active=?")
        params.append(int(body.is_active))
    updates.append("updated_at=?")
    params.append(now_iso())
    params.append(user_id)
    db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
    if body.password is not None or body.role is not None or body.is_active is False:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    audit.log_audit(
        db,
        action="user_update",
        user_id=user.id,
        username=user.username,
        detail=f"目标用户:{target['username']} 变更:"
        + ",".join(
            p for p, changed in [
                ("密码", body.password is not None),
                ("角色", body.role is not None),
                ("启停", body.is_active is not None),
            ] if changed
        ),
        ip=_ip(request),
    )
    fresh = db.execute(
        "SELECT u.id, u.username, CASE WHEN u.role='root' THEN 'root' "
        "WHEN u.is_kb_admin=1 THEN 'kb_admin' ELSE 'user' END AS role, "
        "u.is_active, u.last_login_at, u.created_at, u.updated_at "
        "FROM users u WHERE u.id=?",
        (user_id,),
    ).fetchone()
    return dict(fresh)


# ---------------- 审计 / 全部问答 ----------------

@router.get("/admin/audit")
def list_audit(
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = "", []
    if action:
        where = "WHERE action=?"
        params = [action]
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM audit_logs {where}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"SELECT id, user_id, username, action, detail, ip, created_at "
        f"FROM audit_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/admin/chats")
def list_all_chats(
    user_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = "", []
    if user_id is not None:
        where = "WHERE c.user_id=?"
        params = [user_id]
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM chats c {where}", params
    ).fetchone()["n"]
    rows = db.execute(
        f"SELECT c.id, c.user_id, u.username, c.question, c.answer, c.status, c.error, "
        f"c.model, c.latency_ms, c.prompt_tokens, c.completion_tokens, c.created_at "
        f"FROM chats c JOIN users u ON u.id=c.user_id {where} ORDER BY c.id DESC "
        f"LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/admin/conversations")
def list_all_conversations(
    limit: int = 50,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total = db.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"]
    rows = db.execute(
        "SELECT v.id, v.user_id, u.username, v.title, v.created_at, v.updated_at, "
        "COUNT(c.id) AS turn_count FROM conversations v "
        "JOIN users u ON u.id=v.user_id LEFT JOIN chats c ON c.conversation_id=v.id "
        "GROUP BY v.id ORDER BY v.updated_at DESC, v.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


@router.get("/admin/feedback")
def list_feedback(
    limit: int = 50,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total = db.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"]
    rows = db.execute(
        "SELECT f.id, f.chat_id, f.user_id, u.username, c.question, c.answer, "
        "f.rating, f.comment, f.created_at FROM feedback f "
        "JOIN users u ON u.id=f.user_id JOIN chats c ON c.id=f.chat_id "
        "ORDER BY f.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


@router.get("/admin/feedback.csv")
def export_feedback_csv(
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    rows = db.execute(
        "SELECT f.id, f.chat_id, f.user_id, u.username, c.question, c.answer, "
        "f.rating, f.comment, f.created_at FROM feedback f "
        "JOIN users u ON u.id=f.user_id JOIN chats c ON c.id=f.chat_id "
        "ORDER BY f.id DESC"
    ).fetchall()
    output = io.StringIO()
    output.write("\\ufeff")
    writer = csv.writer(output)
    writer.writerow(["feedback_id", "chat_id", "user_id", "username", "question", "answer", "rating", "comment", "created_at"])
    writer.writerows([tuple(row) for row in rows])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=rag_feedback.csv",
            "Cache-Control": "no-store",
        },
    )


# ---------------- 系统概览 / 运行参数 ----------------

@router.get("/admin/overview")
def overview(db: sqlite3.Connection = Depends(get_db), _=Depends(require_root)):
    counts = {
        "users": db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"],
        "documents": db.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"],
        "chunks": db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"],
        "chats": db.execute("SELECT COUNT(*) AS n FROM chats").fetchone()["n"],
        "uploads_bytes": db.execute(
            "SELECT COALESCE(SUM(size_bytes),0) AS s FROM documents"
        ).fetchone()["s"],
    }
    day_start = datetime.now(timezone.utc).date().isoformat() + "T00:00:00"
    counts["chats_today"] = db.execute(
        "SELECT COUNT(*) AS n FROM chats WHERE created_at>=?", (day_start,)
    ).fetchone()["n"]
    return {
        "counts": counts,
        "model": {
            "version": settings.version,
            "model_ready": embedding_service.state == "ready",
            "model_state": embedding_service.state,
            "model_message": embedding_service.message,
            "embed_model": settings.embed_model,
            "embed_backend": settings.embed_backend,
            "llm_model": settings.deepseek_model,
            "chunk_max_tokens": settings.chunk_max_tokens,
            "chunk_overlap_tokens": settings.chunk_overlap_tokens,
            "min_relevance_score": settings.min_relevance_score,
            "max_upload_mb": settings.max_upload_mb,
            "public_origin": settings.public_origin,
        },
        "settings": rt.get_all(db),
    }


@router.get("/admin/settings")
def get_settings(db: sqlite3.Connection = Depends(get_db), _=Depends(require_root)):
    return {"settings": rt.get_all(db)}


@router.patch("/admin/settings")
def patch_settings(
    body: SettingsPatch,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    if body.top_k is None and body.queries_per_minute is None and body.max_concurrent_llm is None:
        raise HTTPException(status_code=400, detail="没有需要更新的运行参数")
    changes: list[str] = []
    if body.top_k is not None:
        rt.set_value(db, "top_k", body.top_k)
        changes.append(f"top_k={body.top_k}")
    if body.queries_per_minute is not None:
        rt.set_value(db, "queries_per_minute", body.queries_per_minute)
        changes.append(f"queries_per_minute={body.queries_per_minute}")
    if body.max_concurrent_llm is not None:
        rt.set_value(db, "max_concurrent_llm", body.max_concurrent_llm)
        llm_gate.set_max(body.max_concurrent_llm)
        changes.append(f"max_concurrent_llm={body.max_concurrent_llm}")
    audit.log_audit(
        db,
        action="settings_update",
        user_id=user.id,
        username=user.username,
        detail=",".join(changes),
        ip=_ip(request),
    )
    return {"settings": rt.get_all(db)}
