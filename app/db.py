"""SQLite 存储层：每请求独立连接、WAL、外键开启、单写进程模型。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('root','user')),
    is_kb_admin   INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    last_login_at TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT    NOT NULL,
    stored_name  TEXT    NOT NULL UNIQUE,
    content_type TEXT    NOT NULL,
    size_bytes   INTEGER NOT NULL,
    sha256       TEXT    NOT NULL UNIQUE,
    status       TEXT    NOT NULL DEFAULT 'parsing' CHECK (status IN ('parsing','ready','failed')),
    error        TEXT,
    num_chunks   INTEGER NOT NULL DEFAULT 0,
    pages        INTEGER,
    version      TEXT    NOT NULL DEFAULT '1.0',
    effective_date TEXT,
    tags         TEXT    NOT NULL DEFAULT '[]',
    uploaded_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    page        INTEGER,
    paragraph   INTEGER,
    token_count INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    vector      BLOB    NOT NULL,
    UNIQUE (document_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

CREATE TABLE IF NOT EXISTS departments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_bases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_department ON knowledge_bases(department_id);

CREATE TABLE IF NOT EXISTS user_departments (
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, department_id)
);

CREATE TABLE IF NOT EXISTS document_knowledge_bases (
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    knowledge_base_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, knowledge_base_id)
);
CREATE INDEX IF NOT EXISTS idx_doc_kb_kb ON document_knowledge_bases(knowledge_base_id);

CREATE TABLE IF NOT EXISTS chats (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question          TEXT    NOT NULL,
    answer            TEXT    NOT NULL DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
    error             TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    latency_ms        INTEGER,
    created_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_user_time ON chats(user_id, created_at);

CREATE TABLE IF NOT EXISTS chat_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id    INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    score       REAL    NOT NULL,
    page        INTEGER,
    paragraph   INTEGER,
    excerpt     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sources_chat ON chat_sources(chat_id);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating     TEXT    NOT NULL CHECK (rating IN ('helpful','unhelpful')),
    comment    TEXT,
    created_at TEXT    NOT NULL,
    UNIQUE (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_time ON feedback(created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username   TEXT,
    action     TEXT NOT NULL,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    """UTC ISO-8601 秒级时间字符串（可直接做字典序比较）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(path or settings.db_path),
        check_same_thread=False,   # 依赖 FastAPI 对同步端点/依赖的单请求串行保证
        isolation_level=None,      # 每条语句自动提交；入库失败时由业务层清理半成品
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        pass
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI yield dependency：每个请求使用独立连接并在结束时关闭。"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    settings.ensure_dirs()
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _ensure_document_metadata_columns(conn)
        _ensure_user_permission_columns(conn)
    finally:
        conn.close()


def _ensure_document_metadata_columns(conn: sqlite3.Connection) -> None:
    """为旧版数据库补齐新增文档元数据列，迁移可重复执行。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "version" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN version TEXT NOT NULL DEFAULT '1.0'")
    if "effective_date" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN effective_date TEXT")
    if "tags" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")


def _ensure_user_permission_columns(conn: sqlite3.Connection) -> None:
    """为旧版数据库补齐知识库管理员兼容字段。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_kb_admin" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_kb_admin INTEGER NOT NULL DEFAULT 0")


def ensure_scope_defaults(conn: sqlite3.Connection) -> tuple[int, int]:
    """为旧数据建立默认部门/知识库，并补齐未分配的用户和文档。"""
    now = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO departments (name, created_at, updated_at) VALUES ('默认部门', ?, ?)",
        (now, now),
    )
    department_id = conn.execute(
        "SELECT id FROM departments WHERE name='默认部门' COLLATE NOCASE"
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO knowledge_bases (name, department_id, created_at, updated_at) "
        "VALUES ('默认知识库', ?, ?, ?)",
        (department_id, now, now),
    )
    knowledge_base_id = conn.execute(
        "SELECT id FROM knowledge_bases WHERE name='默认知识库' COLLATE NOCASE"
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO user_departments (user_id, department_id) "
        "SELECT u.id, ? FROM users u "
        "WHERE NOT EXISTS (SELECT 1 FROM user_departments ud WHERE ud.user_id=u.id)",
        (department_id,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO document_knowledge_bases (document_id, knowledge_base_id) "
        "SELECT d.id, ? FROM documents d "
        "WHERE NOT EXISTS (SELECT 1 FROM document_knowledge_bases x WHERE x.document_id=d.id)",
        (knowledge_base_id,),
    )
    return int(department_id), int(knowledge_base_id)
