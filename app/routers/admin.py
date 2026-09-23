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

from .. import acl, audit, ingest, metrics, runtime as rt
from ..config import settings
from ..db import get_db, now_iso
from ..deps import require_kb_admin, require_root, stored_role_fields
from ..embeddings import EmbeddingUnavailable, embedding_service
from ..gate import llm_gate
from ..ingest import IngestError
from ..schemas import (
    DocumentAccessBody,
    GroupBody,
    GroupMembersBody,
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
        f"SELECT d.*, u.username AS uploaded_by_name, "
        f"(SELECT COUNT(*) FROM document_acl a "
        f" WHERE a.document_id = d.id AND a.subject_type = 'user') AS granted_user_count, "
        f"(SELECT COUNT(*) FROM document_acl a "
        f" WHERE a.document_id = d.id AND a.subject_type = 'group') AS granted_group_count "
        f"FROM documents d "
        f"LEFT JOIN users u ON u.id = d.uploaded_by "
        f"{where} ORDER BY d.id DESC",
        params,
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        # fail-closed：任何非 shared 的取值都按受限显示。
        item["visibility"] = acl.normalize_visibility(item.get("visibility"))
        items.append(item)
    return items


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
    visibility: str = Form(default="shared"),
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    filename = _clean_filename(file.filename)
    version = _clean_version(version)
    visibility = acl.normalize_visibility(visibility)
    _require_visibility_permission(user, visibility)
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
            visibility=visibility,
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


# ---------------- 文档可见范围（只有 root 能决定"谁能看到什么"） ----------------
#
# 权限划分口径：kb_admin 负责**文档内容**（上传/重建/删除），root 负责**访问控制**。
# 这样既符合既有的角色契约（文档管理员不管理用户与系统配置），也避免出现
# "文档管理员先把机密文件当共享传上去，事后再改"的暴露窗口。

def _require_visibility_permission(user, visibility: str) -> None:
    if visibility != acl.SHARED and user.role != "root":
        raise HTTPException(
            status_code=403,
            detail="只有 root 能设置文档可见范围；文档管理员上传的文档默认对全员可见。",
        )


@router.get("/admin/documents/{doc_id}/access")
def get_document_access(
    doc_id: int,
    db: sqlite3.Connection = Depends(get_db),
    _=Depends(require_root),
):
    row = db.execute(
        "SELECT id, filename, visibility FROM documents WHERE id=?", (doc_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {
        "document_id": int(row["id"]),
        "filename": row["filename"],
        "visibility": acl.normalize_visibility(row["visibility"]),
        "granted_user_ids": acl.granted_user_ids(db, doc_id),
        "granted_group_ids": acl.granted_group_ids(db, doc_id),
        "candidates": acl.grantable_users(db),
        "group_candidates": acl.list_groups(db),
    }


@router.put("/admin/documents/{doc_id}/access")
def update_document_access(
    doc_id: int,
    body: DocumentAccessBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    row = db.execute(
        "SELECT id, filename, visibility FROM documents WHERE id=?", (doc_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    valid_user_ids = {item["id"] for item in acl.grantable_users(db)}
    unknown_users = sorted(set(body.user_ids) - valid_user_ids)
    if unknown_users:
        raise HTTPException(
            status_code=422,
            detail=f"以下用户不存在或不可被单独授权（root 本来就不受限）：{unknown_users}",
        )

    valid_group_ids = {item["id"] for item in acl.list_groups(db)}
    unknown_groups = sorted(set(body.group_ids) - valid_group_ids)
    if unknown_groups:
        raise HTTPException(status_code=422, detail=f"以下用户组不存在：{unknown_groups}")

    if body.visibility == acl.RESTRICTED and not (body.user_ids or body.group_ids):
        # 受限但一个授权主体都没有，等于"只有管理员能看"，几乎总是误操作，
        # 而且会让设置的人自己也用不了。直接拒绝并给出正确做法。
        raise HTTPException(
            status_code=422,
            detail="受限文档必须至少指定一个可见账号或用户组；若希望全员可见，请选择 shared。",
        )

    acl.set_document_access(
        db, doc_id, body.visibility, body.user_ids, body.group_ids, user.id, now_iso()
    )
    audit.log_audit(
        db,
        action="document_access_update",
        user_id=user.id,
        username=user.username,
        detail=(
            f"doc:{doc_id} 文件:{row['filename']} "
            f"可见范围:{acl.normalize_visibility(body.visibility)} "
            f"授权账号:{len(body.user_ids)} 授权组:{len(body.group_ids)}"
        ),
        ip=_ip(request),
    )
    return {
        "document_id": doc_id,
        "visibility": acl.normalize_visibility(body.visibility),
        "granted_user_ids": acl.granted_user_ids(db, doc_id),
        "granted_group_ids": acl.granted_group_ids(db, doc_id),
    }


# ---------------- 用户组管理（只有 root；组是访问控制的载体） ----------------

def _clean_group_name(raw: str | None) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="组名不能为空")
    if len(name) > 32:
        raise HTTPException(status_code=422, detail="组名最长 32 个字符")
    return name


def _require_group(db: sqlite3.Connection, group_id: int):
    row = db.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="用户组不存在")
    return row


@router.get("/admin/groups")
def list_groups(db: sqlite3.Connection = Depends(get_db), _=Depends(require_root)):
    items = acl.list_groups(db)
    return {
        "items": items,
        "total": len(items),
        # 成员候选人复用"可授权账号"清单（不含 root，它本来就不受限）。
        "candidates": acl.grantable_users(db),
    }


@router.post("/admin/groups", status_code=201)
def create_group(
    body: GroupBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    name = _clean_group_name(body.name)
    try:
        group_id = acl.create_group(db, name, body.description, now_iso())
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"用户组「{name}」已存在") from None
    audit.log_audit(
        db,
        action="group_create",
        user_id=user.id,
        username=user.username,
        detail=f"group:{group_id} 名称:{name}",
        ip=_ip(request),
    )
    return {"id": group_id, "name": name, "description": body.description, "member_ids": []}


@router.patch("/admin/groups/{group_id}")
def update_group(
    group_id: int,
    body: GroupBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    _require_group(db, group_id)
    name = _clean_group_name(body.name)
    try:
        acl.update_group(db, group_id, name, body.description, now_iso())
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"用户组「{name}」已存在") from None
    audit.log_audit(
        db,
        action="group_update",
        user_id=user.id,
        username=user.username,
        detail=f"group:{group_id} 名称:{name}",
        ip=_ip(request),
    )
    return {"id": group_id, "name": name, "description": body.description}


@router.put("/admin/groups/{group_id}/members")
def set_group_members(
    group_id: int,
    body: GroupMembersBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    group = _require_group(db, group_id)
    valid_ids = {item["id"] for item in acl.grantable_users(db)}
    unknown = sorted(set(body.user_ids) - valid_ids)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"以下用户不存在或不能加入用户组（root 本来就不受限）：{unknown}",
        )
    acl.set_group_members(db, group_id, body.user_ids, now_iso())
    # 成员变化会直接改变一批文档的可见性，因此审计里带上当前生效的授权文档数，
    # 便于事后回答"这次改动到底影响了什么"。
    granted = len(
        db.execute(
            "SELECT 1 FROM document_acl WHERE subject_type=? AND subject_id=?",
            (acl.SUBJECT_GROUP, group_id),
        ).fetchall()
    )
    audit.log_audit(
        db,
        action="group_members_update",
        user_id=user.id,
        username=user.username,
        detail=(
            f"group:{group_id} 名称:{group['name']} "
            f"成员数:{len(body.user_ids)} 该组已授权文档数:{granted}"
        ),
        ip=_ip(request),
    )
    return {"id": group_id, "member_ids": sorted({int(x) for x in body.user_ids})}


@router.delete("/admin/groups/{group_id}", status_code=204)
def delete_group(
    group_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_root),
):
    group = _require_group(db, group_id)
    affected = len(
        db.execute(
            "SELECT 1 FROM document_acl WHERE subject_type=? AND subject_id=?",
            (acl.SUBJECT_GROUP, group_id),
        ).fetchall()
    )
    acl.delete_group(db, group_id)
    audit.log_audit(
        db,
        action="group_delete",
        user_id=user.id,
        username=user.username,
        detail=(
            f"group:{group_id} 名称:{group['name']} "
            f"同时清理了 {affected} 份文档对该组的授权"
        ),
        ip=_ip(request),
    )
    return Response(status_code=204)


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

def _latency_block(db: sqlite3.Connection) -> dict:
    """最近若干轮问答的分阶段耗时统计。

    只取最近 N 轮而不是固定时长：查询成本与数据库大小无关，也不会因为长期没人提问
    而返回空统计。口径（只统计真正发起过模型调用的轮次、拒答与失败单独计数）写在
    app/metrics.py 里，避免在这里再次解释一遍而产生分歧。
    """
    rows = db.execute(
        "SELECT status, latency_ms, retrieval_ms, completion_tokens, created_at"
        " FROM chats ORDER BY id DESC LIMIT ?",
        (metrics.DEFAULT_WINDOW,),
    ).fetchall()
    return metrics.latency_summary([dict(row) for row in rows], window=metrics.DEFAULT_WINDOW)


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
        "latency": _latency_block(db),
        "model": {
            "version": settings.version,
            "model_ready": embedding_service.state == "ready",
            "model_state": embedding_service.state,
            "model_message": embedding_service.message,
            "embed_model": settings.embed_model,
            "embed_backend": settings.embed_backend,
            "embed_device": embedding_service.device,
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
