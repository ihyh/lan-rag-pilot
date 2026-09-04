"""登录 / 注销 / 当前用户 / 修改密码。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import audit
from ..config import settings
from ..db import get_db, now_iso
from ..deps import CurrentUser, current_user_or_none, logical_role, require_user
from ..embeddings import embedding_service
from ..ratelimit import SlidingWindowLimiter
from ..schemas import LoginBody, PasswordBody
from ..security import (
    SESSION_COOKIE,
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)

router = APIRouter()

_login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60.0)


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


@router.post("/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
):
    ip = _ip(request)
    username_l = body.username.strip().lower()
    ok, retry = _login_limiter.allow(key=f"login:{ip}:{username_l}")
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过于频繁，请约 {int(retry) + 1} 秒后再试",
        )

    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))  # 顺手清理过期会话
    row = db.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE", (body.username.strip(),)
    ).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        audit.log_audit(
            db,
            action="login_failed",
            username=username_l,
            detail="用户名或密码错误",
            ip=ip,
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not row["is_active"]:
        audit.log_audit(
            db,
            action="login_blocked",
            user_id=row["id"],
            username=row["username"],
            detail="账号已停用",
            ip=ip,
        )
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")

    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    db.execute(
        "INSERT INTO sessions (user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
        (row["id"], hash_session_token(token), now_iso(), expires.isoformat(timespec="seconds")),
    )
    db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), row["id"]))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    audit.log_audit(
        db,
        action="login",
        user_id=row["id"],
        username=row["username"],
        ip=ip,
    )
    return {
        "ok": True,
        "user": {
            "username": row["username"],
            "role": logical_role(row["role"], bool(row["is_kb_admin"])),
        },
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(current_user_or_none),
):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_session_token(token),))
        if user is not None:
            audit.log_audit(
                db,
                action="logout",
                user_id=user.id,
                username=user.username,
                ip=_ip(request),
            )
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user_or_none)):
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return {
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "model_ready": embedding_service.state == "ready",
        "model_message": embedding_service.message or None,
    }


@router.post("/me/password")
def change_password(
    body: PasswordBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user: CurrentUser = Depends(require_user),
):
    row = db.execute("SELECT password_hash FROM users WHERE id=?", (user.id,)).fetchone()
    if not verify_password(body.old_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    db.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
        (hash_password(body.new_password), now_iso(), user.id),
    )
    # 让其它会话失效（保留当前会话）
    cur_hash = hash_session_token(request.cookies.get(SESSION_COOKIE, "")) \
        if request.cookies.get(SESSION_COOKIE) else None
    if cur_hash:
        db.execute(
            "DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user.id, cur_hash)
        )
    else:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user.id,))
    audit.log_audit(
        db,
        action="password_change",
        user_id=user.id,
        username=user.username,
        ip=_ip(request),
    )
    return {"ok": True}
