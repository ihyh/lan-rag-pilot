"""文档级授权：集中判定「谁能看到哪些文档」。

设计口径（三条，均在 SECURITY.md 与 README 中说明）：

1. **默认共享**：`documents.visibility` 默认 `shared`，所有启用账号可见。
   旧库升级后现有文档全部保持 `shared`，不会因为一次升级而静默收紧权限。
2. **fail-closed**：只有取值恰好等于 `shared` 才视为公开；其它任何取值
   （包括数据被改坏、写入异常值）都按受限处理，必须存在显式授权才可见。
3. **管理员例外**：root 与 kb_admin 可见全部文档。否则受限文档无法被管理
   （上传、重建索引、删除都需要看到它）。这一例外在打开受限原文时记入审计，
   以便事后核查——即 ROADMAP 提到的"管理员例外权限审计"。

授权主体建成通用形式（`subject_type` / `subject_id`），当前只实现按用户授权；
将来要加"按组授权"只需新增 subject_type 取值，不必再改表结构。
"""
from __future__ import annotations

import sqlite3

from .deps import CurrentUser

SHARED = "shared"
RESTRICTED = "restricted"
SUBJECT_USER = "user"


def normalize_visibility(raw: str | None) -> str:
    """只接受 shared / restricted；其它一律按 restricted 处理（fail-closed）。"""
    value = (raw or "").strip().lower()
    return SHARED if value == SHARED else RESTRICTED


def is_admin(user: CurrentUser) -> bool:
    """root 与 kb_admin 不受文档级授权限制。"""
    return getattr(user, "role", "") in ("root", "kb_admin")


def visible_document_ids(db: sqlite3.Connection, user: CurrentUser) -> set[int] | None:
    """该用户可见的文档 id 集合；`None` 表示不受限（管理员）。

    返回的是**全部状态**的文档 id（含 parsing / failed）。调用方若还需要
    `status='ready'`，应在此基础上再与 ready 集合求交。
    """
    if is_admin(user):
        return None

    shared = {
        int(row["id"])
        for row in db.execute("SELECT id FROM documents WHERE visibility = ?", (SHARED,))
    }
    granted = {
        int(row["document_id"])
        for row in db.execute(
            "SELECT document_id FROM document_acl WHERE subject_type = ? AND subject_id = ?",
            (SUBJECT_USER, int(user.id)),
        )
    }
    return shared | granted


def can_access(db: sqlite3.Connection, user: CurrentUser, document_id: int) -> bool:
    allowed = visible_document_ids(db, user)
    return allowed is None or int(document_id) in allowed


def is_restricted(db: sqlite3.Connection, document_id: int) -> bool:
    row = db.execute("SELECT visibility FROM documents WHERE id = ?", (document_id,)).fetchone()
    return row is not None and normalize_visibility(row["visibility"]) == RESTRICTED


def effective_scope(
    db: sqlite3.Connection,
    user: CurrentUser,
    requested: list[int] | None,
) -> set[int] | None:
    """把「用户请求的文档范围」与「他实际可见的范围」合并。

    - `requested` 为空表示不限定（全库）；
    - 返回值 `None` 表示不限定且用户是管理员；
    - 返回值可能为空集合，调用方应据此判定「没有可见文档」而不是「全库」。
    """
    allowed = visible_document_ids(db, user)
    if not requested:
        return allowed
    scope = {int(x) for x in requested}
    if allowed is None:
        return scope
    return scope & allowed


def grantable_users(db: sqlite3.Connection) -> list[dict]:
    """可被单独授权的账号。root 不在其中——它本来就不受限。"""
    rows = db.execute(
        "SELECT u.id, u.username, "
        "CASE WHEN u.role='root' THEN 'root' "
        "WHEN u.is_kb_admin=1 THEN 'kb_admin' ELSE 'user' END AS role, "
        "u.is_active FROM users u ORDER BY u.username"
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "username": row["username"],
            "role": row["role"],
            "is_active": bool(row["is_active"]),
        }
        for row in rows
        if row["role"] != "root"
    ]


def granted_user_ids(db: sqlite3.Connection, document_id: int) -> list[int]:
    return sorted(
        int(row["subject_id"])
        for row in db.execute(
            "SELECT subject_id FROM document_acl WHERE document_id = ? AND subject_type = ?",
            (document_id, SUBJECT_USER),
        )
    )


def set_document_access(
    db: sqlite3.Connection,
    document_id: int,
    visibility: str,
    user_ids: list[int],
    granted_by: int,
    now: str,
) -> None:
    """整体替换某份文档的授权名单，并写入可见范围。

    采用"整体替换"而不是增量增删：管理员在界面上看到的是一份勾选清单，
    提交后以该清单为准，避免出现"界面上取消了但其实还在库里"的状态。
    """
    db.execute(
        "UPDATE documents SET visibility = ?, updated_at = ? WHERE id = ?",
        (normalize_visibility(visibility), now, document_id),
    )
    db.execute(
        "DELETE FROM document_acl WHERE document_id = ? AND subject_type = ?",
        (document_id, SUBJECT_USER),
    )
    if normalize_visibility(visibility) == SHARED:
        # 全员可见时保留授权名单没有意义，反而会在将来改回受限时造成"意外仍可见"。
        return
    for user_id in sorted({int(x) for x in user_ids}):
        db.execute(
            "INSERT OR IGNORE INTO document_acl "
            "(document_id, subject_type, subject_id, granted_by, created_at) VALUES (?,?,?,?,?)",
            (document_id, SUBJECT_USER, user_id, granted_by, now),
        )
