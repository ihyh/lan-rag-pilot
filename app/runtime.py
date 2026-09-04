"""运行时可调配置：优先取 settings 表覆盖值，缺省回退到环境默认值。

root 在管理界面（PATCH /api/admin/settings）可改 top_k / queries_per_minute /
max_concurrent_llm；进程重启后以表中留存值为准。
"""
from __future__ import annotations

import sqlite3

from .config import settings
from .db import now_iso

KEYS = ("top_k", "queries_per_minute", "max_concurrent_llm")

_DEFAULTS = {
    "top_k": settings.top_k,
    "queries_per_minute": settings.queries_per_minute,
    "max_concurrent_llm": settings.max_concurrent_llm,
}


def get_all(db: sqlite3.Connection) -> dict[str, int]:
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    out: dict[str, int] = {}
    for key in KEYS:
        raw = rows.get(key)
        try:
            out[key] = int(raw) if raw is not None else int(_DEFAULTS[key])
        except (TypeError, ValueError):
            out[key] = int(_DEFAULTS[key])
    return out


def set_value(db: sqlite3.Connection, key: str, value: int) -> None:
    db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, str(int(value)), now_iso()),
    )
