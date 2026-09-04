"""root 管理接口：文档 / 用户 / 审计 / 系统概览与运行参数。"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from .. import audit, ingest, runtime as rt
from ..config import settings
from ..db import ensure_scope_defaults, get_db, now_iso
from ..deps import require_root, require_user
from ..embeddings import EmbeddingUnavailable, embedding_service
from ..gate import llm_gate
from ..ingest import IngestError
from ..schemas import (
    DepartmentCreate,
    DocumentScopeBody,
    KnowledgeBaseCreate,
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


def _clean_effective_date(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="生效日期须为 YYYY-MM-DD") from None
    return value


def _clean_tags(raw: str | None) -> list[str]:
    values = []
    for item in re.split(r"[,，]", raw or ""):
        tag = item.strip()
        if not tag:
            continue
        if len(tag) > 32 or any(ord(char) < 32 for char in tag):
            raise HTTPException(status_code=422, detail="单个标签须为 1-32 个可见字符")
        if tag.casefold() not in {existing.casefold() for existing in values}:
            values.append(tag)
    if len(values) > 10:
        raise HTTPException(status_code=422, detail="每个文档最多设置 10 个标签")
    return values


def _decode_tags(raw: str | None) -> list[str]:
    try:
        tags = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return tags if isinstance(tags, list) else []


def _raise_ingest(exc: IngestError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _raise_embed(exc: EmbeddingUnavailable) -> None:
    raise HTTPException(
        status_code=503, detail={"code": "embed_not_ready", "message": str(exc)}
    ) from exc


def _document_rows(db: sqlite3.Connection, where: str = "", params: tuple = ()) -> list[dict]:
    rows = db.execute(
        f"SELECT d.*, u.username AS uploaded_by_name, "
        f"COALESCE((SELECT group_concat(kb.name, '、') FROM document_knowledge_bases dkb "
        f"JOIN knowledge_bases kb ON kb.id=dkb.knowledge_base_id WHERE dkb.document_id=d.id), '') "
        f"AS knowledge_base_names, COALESCE((SELECT group_concat(kb.id, ',') FROM document_knowledge_bases dkb "
        f"JOIN knowledge_bases kb ON kb.id=dkb.knowledge_base_id WHERE dkb.document_id=d.id), '') "
        f"AS knowledge_base_ids FROM documents d LEFT JOIN users u ON u.id = d.uploaded_by "
        f"{where} ORDER BY d.id DESC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------- 文档管理 ----------------

@router.get("/admin/documents")
def list_documents(
    version: str | None = Query(default=None, max_length=32),
    tag: str | None = Query(default=None, max_length=32),
    effective_date_from: str | None = Query(default=None),
    effective_date_to: str | None = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    where: list[str] = []
    params: list[str] = []
    if version:
        where.append("d.version=?")
        params.append(_clean_version(version))
    date_from = _clean_effective_date(effective_date_from)
    date_to = _clean_effective_date(effective_date_to)
    if date_from:
        where.append("d.effective_date>=?")
        params.append(date_from)
    if date_to:
        where.append("d.effective_date<=?")
        params.append(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="生效日期起始值不能晚于结束值")
    clause = "WHERE " + " AND ".join(where) if where else ""
    items = _document_rows(db, clause, tuple(params))
    if tag:
        clean_filter = _clean_tags(tag)
        if len(clean_filter) != 1:
            raise HTTPException(status_code=422, detail="标签筛选只允许填写一个标签")
        wanted = clean_filter[0].casefold()
        items = [item for item in items if any(str(value).casefold() == wanted for value in _decode_tags(item.get("tags")))]
    for item in items:
        item["tags"] = _decode_tags(item.get("tags"))
    return {"items": items, "total": len(items)}


@router.post("/admin/documents", status_code=201)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(default="1.0"),
    effective_date: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    filename = _clean_filename(file.filename)
    version = _clean_version(version)
    effective_date = _clean_effective_date(effective_date)
    tags = _clean_tags(tags)
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
        with ingest.ingest_lock:
            doc = ingest.ingest_bytes(
                db,
                filename=filename,
                content_type=file.content_type or "",
                data=data,
                user_id=user.id,
                version=version,
                effective_date=effective_date,
                tags=tags,
            )
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
        detail=f"doc:{doc['id']} 文件:{filename} 版本:{version} 生效:{effective_date or '未设置'} 标签:{','.join(tags) or '无'} 切片数:{doc['num_chunks']}",
        ip=_ip(request),
    )
    _, default_kb_id = ensure_scope_defaults(db)
    db.execute(
        "INSERT OR IGNORE INTO document_knowledge_bases (document_id, knowledge_base_id) VALUES (?,?)",
        (doc["id"], default_kb_id),
    )
    return doc


@router.delete("/admin/documents/{doc_id}", status_code=204)
def delete_document(
    doc_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
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
    user=Depends(require_root),
):
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
        "SELECT u.id, u.username, u.role, u.is_active, u.last_login_at, u.created_at, u.updated_at, "
        "COALESCE((SELECT group_concat(d.name, '、') FROM user_departments ud "
        "JOIN departments d ON d.id=ud.department_id WHERE ud.user_id=u.id), '') AS department_names, "
        "COALESCE((SELECT group_concat(d.id, ',') FROM user_departments ud "
        "JOIN departments d ON d.id=ud.department_id WHERE ud.user_id=u.id), '') AS department_ids "
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
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)"
            " VALUES (?,?,?,1,?,?)",
            (body.username, hash_password(body.password), body.role, now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="用户名已存在") from None
    default_department_id, _ = ensure_scope_defaults(db)
    db.execute(
        "INSERT OR IGNORE INTO user_departments (user_id, department_id) VALUES (?,?)",
        (int(cur.lastrowid), default_department_id),
    )
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
    if not any([body.password, body.role, body.is_active is not None, body.department_ids is not None]):
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    if user_id == user.id:
        if (body.role is not None and body.role != "root") or body.is_active is False:
            raise HTTPException(status_code=400, detail="不能停用或降级自己的账号")

    next_role = body.role or target["role"]
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
        updates.append("role=?")
        params.append(body.role)
    if body.is_active is not None:
        updates.append("is_active=?")
        params.append(int(body.is_active))
    if body.department_ids is not None:
        if not body.department_ids:
            raise HTTPException(status_code=400, detail="至少分配一个部门")
        placeholders = ",".join("?" for _ in body.department_ids)
        valid = db.execute(
            f"SELECT COUNT(*) AS n FROM departments WHERE id IN ({placeholders})",
            body.department_ids,
        ).fetchone()["n"]
        if valid != len(set(body.department_ids)):
            raise HTTPException(status_code=400, detail="包含不存在的部门")
    updates.append("updated_at=?")
    params.append(now_iso())
    params.append(user_id)
    db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
    if body.department_ids is not None:
        db.execute("DELETE FROM user_departments WHERE user_id=?", (user_id,))
        db.executemany(
            "INSERT INTO user_departments (user_id, department_id) VALUES (?,?)",
            [(user_id, department_id) for department_id in sorted(set(body.department_ids))],
        )
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
                ("部门", body.department_ids is not None),
            ] if changed
        ),
        ip=_ip(request),
    )
    fresh = db.execute(
        "SELECT u.id, u.username, u.role, u.is_active, u.last_login_at, u.created_at, u.updated_at, "
        "COALESCE((SELECT group_concat(d.name, '、') FROM user_departments ud "
        "JOIN departments d ON d.id=ud.department_id WHERE ud.user_id=u.id), '') AS department_names, "
        "COALESCE((SELECT group_concat(d.id, ',') FROM user_departments ud "
        "JOIN departments d ON d.id=ud.department_id WHERE ud.user_id=u.id), '') AS department_ids "
        "FROM users u WHERE u.id=?",
        (user_id,),
    ).fetchone()
    return dict(fresh)


# ---------------- 部门 / 知识库权限 ----------------

@router.get("/knowledge-bases")
def list_accessible_knowledge_bases(
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    if user.role == "root":
        rows = db.execute(
            "SELECT kb.id, kb.name, kb.department_id, d.name AS department_name "
            "FROM knowledge_bases kb JOIN departments d ON d.id=kb.department_id "
            "ORDER BY d.name, kb.name"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT DISTINCT kb.id, kb.name, kb.department_id, d.name AS department_name "
            "FROM knowledge_bases kb JOIN departments d ON d.id=kb.department_id "
            "JOIN user_departments ud ON ud.department_id=kb.department_id "
            "WHERE ud.user_id=? ORDER BY d.name, kb.name",
            (user.id,),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/admin/departments")
def list_departments(db: sqlite3.Connection = Depends(get_db), _=Depends(require_root)):
    rows = db.execute(
        "SELECT d.id, d.name, d.created_at, d.updated_at, "
        "(SELECT COUNT(*) FROM user_departments ud WHERE ud.department_id=d.id) AS user_count, "
        "(SELECT COUNT(*) FROM knowledge_bases kb WHERE kb.department_id=d.id) AS knowledge_base_count "
        "FROM departments d ORDER BY d.name"
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/admin/departments", status_code=201)
def create_department(
    body: DepartmentCreate,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    now = now_iso()
    try:
        cur = db.execute(
            "INSERT INTO departments (name, created_at, updated_at) VALUES (?,?,?)",
            (body.name, now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="部门名称已存在") from None
    audit.log_audit(db, action="department_create", user_id=user.id, username=user.username,
                    detail=f"部门:{body.name}", ip=_ip(request))
    return {"id": int(cur.lastrowid), "name": body.name, "created_at": now, "updated_at": now}


@router.get("/admin/knowledge-bases")
def list_knowledge_bases(db: sqlite3.Connection = Depends(get_db), _=Depends(require_root)):
    rows = db.execute(
        "SELECT kb.id, kb.name, kb.department_id, d.name AS department_name, "
        "kb.created_at, kb.updated_at, "
        "(SELECT COUNT(*) FROM document_knowledge_bases dkb WHERE dkb.knowledge_base_id=kb.id) AS document_count "
        "FROM knowledge_bases kb JOIN departments d ON d.id=kb.department_id "
        "ORDER BY d.name, kb.name"
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/admin/knowledge-bases", status_code=201)
def create_knowledge_base(
    body: KnowledgeBaseCreate,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    if db.execute("SELECT 1 FROM departments WHERE id=?", (body.department_id,)).fetchone() is None:
        raise HTTPException(status_code=400, detail="部门不存在")
    now = now_iso()
    try:
        cur = db.execute(
            "INSERT INTO knowledge_bases (name, department_id, created_at, updated_at) VALUES (?,?,?,?)",
            (body.name, body.department_id, now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="知识库名称已存在") from None
    audit.log_audit(db, action="knowledge_base_create", user_id=user.id, username=user.username,
                    detail=f"知识库:{body.name} 部门:{body.department_id}", ip=_ip(request))
    return {"id": int(cur.lastrowid), "name": body.name, "department_id": body.department_id,
            "created_at": now, "updated_at": now}


@router.patch("/admin/documents/{doc_id}/knowledge-bases")
def assign_document_knowledge_bases(
    doc_id: int,
    body: DocumentScopeBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    if db.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    ids = sorted(set(body.knowledge_base_ids))
    placeholders = ",".join("?" for _ in ids)
    valid = db.execute(
        f"SELECT COUNT(*) AS n FROM knowledge_bases WHERE id IN ({placeholders})", ids
    ).fetchone()["n"]
    if valid != len(ids):
        raise HTTPException(status_code=400, detail="包含不存在的知识库")
    db.execute("DELETE FROM document_knowledge_bases WHERE document_id=?", (doc_id,))
    db.executemany(
        "INSERT INTO document_knowledge_bases (document_id, knowledge_base_id) VALUES (?,?)",
        [(doc_id, kb_id) for kb_id in ids],
    )
    audit.log_audit(db, action="document_scope_update", user_id=user.id, username=user.username,
                    detail=f"文档:{doc_id} 知识库:{','.join(map(str, ids))}", ip=_ip(request))
    return {"document_id": doc_id, "knowledge_base_ids": ids}


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
