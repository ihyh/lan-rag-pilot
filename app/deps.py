"""认证相关 FastAPI 依赖。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from .db import get_db, now_iso
from .security import SESSION_COOKIE, hash_session_token


@dataclass
class CurrentUser:
    id: int
    username: str
    role: str
    is_active: bool


def current_user_or_none(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
):
    """从 Cookie 会话解析当前用户；无有效会话返回 None（不抛错）。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    token_hash = hash_session_token(token)
    row = db.execute(
        "SELECT u.id, u.username, u.role, u.is_active FROM sessions s "
        "JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash=? AND s.expires_at > ?",
        (token_hash, now_iso()),
    ).fetchone()
    if row is None:
        return None
    return CurrentUser(
        id=int(row["id"]),
        username=row["username"],
        role=row["role"],
        is_active=bool(row["is_active"]),
    )


def require_user(user=Depends(current_user_or_none)) -> CurrentUser:
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    return user


def require_root(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if user.role != "root":
        raise HTTPException(status_code=403, detail="需要 root 权限")
    return user
