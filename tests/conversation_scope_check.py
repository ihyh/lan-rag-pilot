"""Synthetic conversation-scope migration and validation checks."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module
from app.config import settings
from app.schemas import QueryBody


def main() -> None:
    body = QueryBody(question="测试问题", document_ids=[3, 2, 3])
    assert body.document_ids == [2, 3]

    original = (settings.data_dir, settings.db_path, settings.upload_dir, settings.models_dir)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        settings.data_dir = root
        settings.db_path = root / "legacy.db"
        settings.upload_dir = root / "uploads"
        settings.models_dir = root / "models"
        try:
            legacy_schema = db_module.SCHEMA.replace(
                "    document_ids TEXT    NOT NULL DEFAULT '[]',\n", ""
            )
            with closing(sqlite3.connect(settings.db_path)) as database:
                database.executescript(legacy_schema)
                database.execute(
                    "INSERT INTO users (username,password_hash,role,created_at,updated_at) "
                    "VALUES ('legacy','hash','user','2026-01-01','2026-01-01')"
                )
                database.execute(
                    "INSERT INTO conversations (user_id,title,created_at,updated_at) "
                    "VALUES (1,'legacy','2026-01-01','2026-01-01')"
                )
                database.commit()
            db_module.init_db()
            db_module.init_db()
            with closing(sqlite3.connect(settings.db_path)) as database:
                columns = {row[1] for row in database.execute("PRAGMA table_info(conversations)")}
                stored = database.execute(
                    "SELECT document_ids FROM conversations WHERE id=1"
                ).fetchone()[0]
            assert "document_ids" in columns
            assert stored == "[]"
        finally:
            settings.data_dir, settings.db_path, settings.upload_dir, settings.models_dir = original

    print("Conversation scope check: PASS (validation, legacy migration, idempotency)")


if __name__ == "__main__":
    main()
