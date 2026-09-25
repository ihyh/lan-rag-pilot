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
SUBJECT_GROUP = "group"


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
    # 按组授权：用户所属的每个组，其被授权的文档都对该用户可见。
    # 组成员关系变化后立即生效（每次判定都现查，不缓存），因此从组里移除成员
    # 就等于撤销这批文档的访问权。
    granted |= {
        int(row["document_id"])
        for row in db.execute(
            "SELECT a.document_id FROM document_acl a "
            "JOIN group_members m ON m.group_id = a.subject_id "
            "WHERE a.subject_type = ? AND m.user_id = ?",
            (SUBJECT_GROUP, int(user.id)),
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


def granted_subject_ids(db: sqlite3.Connection, document_id: int, subject_type: str) -> list[int]:
    return sorted(
        int(row["subject_id"])
        for row in db.execute(
            "SELECT subject_id FROM document_acl WHERE document_id = ? AND subject_type = ?",
            (document_id, subject_type),
        )
    )


def granted_user_ids(db: sqlite3.Connection, document_id: int) -> list[int]:
    return granted_subject_ids(db, document_id, SUBJECT_USER)


def granted_group_ids(db: sqlite3.Connection, document_id: int) -> list[int]:
    return granted_subject_ids(db, document_id, SUBJECT_GROUP)


def set_document_access(
    db: sqlite3.Connection,
    document_id: int,
    visibility: str,
    user_ids: list[int],
    group_ids: list[int],
    granted_by: int,
    now: str,
) -> None:
    """整体替换某份文档的授权名单（用户与组），并写入可见范围。

    采用"整体替换"而不是增量增删：管理员在界面上看到的是一份勾选清单，
    提交后以该清单为准，避免出现"界面上取消了但其实还在库里"的状态。
    """
    visibility = normalize_visibility(visibility)
    db.execute(
        "UPDATE documents SET visibility = ?, updated_at = ? WHERE id = ?",
        (visibility, now, document_id),
    )
    db.execute("DELETE FROM document_acl WHERE document_id = ?", (document_id,))
    if visibility == SHARED:
        # 全员可见时保留授权名单没有意义，反而会在将来改回受限时造成"意外仍可见"。
        return
    for subject_type, ids in ((SUBJECT_USER, user_ids), (SUBJECT_GROUP, group_ids)):
        for subject_id in sorted({int(x) for x in ids}):
            db.execute(
                "INSERT OR IGNORE INTO document_acl "
                "(document_id, subject_type, subject_id, granted_by, created_at) VALUES (?,?,?,?,?)",
                (document_id, subject_type, subject_id, granted_by, now),
            )


# ---------------- 用户组 ----------------

def list_groups(db: sqlite3.Connection) -> list[dict]:
    """列出全部组及其成员，并附带"该组被授权了多少份文档"，便于管理员判断影响面。"""
    groups = db.execute(
        "SELECT g.id, g.name, g.description, g.created_at, g.updated_at, "
        "(SELECT COUNT(*) FROM document_acl a "
        " WHERE a.subject_type = ? AND a.subject_id = g.id) AS granted_document_count "
        "FROM groups g ORDER BY g.name COLLATE NOCASE, g.id",
        (SUBJECT_GROUP,),
    ).fetchall()
    members: dict[int, list[int]] = {}
    for row in db.execute("SELECT group_id, user_id FROM group_members ORDER BY user_id"):
        members.setdefault(int(row["group_id"]), []).append(int(row["user_id"]))
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "description": row["description"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "member_ids": members.get(int(row["id"]), []),
            "granted_document_count": int(row["granted_document_count"]),
        }
        for row in groups
    ]


def create_group(db: sqlite3.Connection, name: str, description: str, now: str) -> int:
    cur = db.execute(
        "INSERT INTO groups (name, description, created_at, updated_at) VALUES (?,?,?,?)",
        (name, description, now, now),
    )
    return int(cur.lastrowid)


def update_group(db: sqlite3.Connection, group_id: int, name: str, description: str, now: str) -> None:
    db.execute(
        "UPDATE groups SET name = ?, description = ?, updated_at = ? WHERE id = ?",
        (name, description, now, group_id),
    )


def delete_group(db: sqlite3.Connection, group_id: int) -> None:
    """删除组，并清理它的文档授权。

    document_acl.subject_id 是通用主体，没有外键级联，必须在这里显式清理；
    否则会留下指向已删组的悬空授权（虽然当前不会命中，但属于脏数据）。
    """
    db.execute(
        "DELETE FROM document_acl WHERE subject_type = ? AND subject_id = ?",
        (SUBJECT_GROUP, group_id),
    )
    db.execute("DELETE FROM groups WHERE id = ?", (group_id,))


def set_group_members(db: sqlite3.Connection, group_id: int, user_ids: list[int], now: str) -> None:
    db.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
    for user_id in sorted({int(x) for x in user_ids}):
        db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id, created_at) VALUES (?,?,?)",
            (group_id, user_id, now),
        )
    db.execute("UPDATE groups SET updated_at = ? WHERE id = ?", (now, group_id))


def group_ids_for_user(db: sqlite3.Connection, user_id: int) -> list[int]:
    return sorted(
        int(row["group_id"])
        for row in db.execute(
            "SELECT group_id FROM group_members WHERE user_id = ?", (int(user_id),)
        )
    )
