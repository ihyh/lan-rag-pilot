"""审计日志写入助手。"""
from __future__ import annotations

import sqlite3

from .db import now_iso


def log_audit(
    db: sqlite3.Connection,
    *,
    action: str,
    user_id: int | None = None,
    username: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO audit_logs (user_id, username, action, detail, ip, created_at) VALUES (?,?,?,?,?,?)",
        (user_id, username, action, detail, ip, now_iso()),
    )
