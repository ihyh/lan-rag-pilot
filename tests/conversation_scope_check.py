"""Synthetic conversation-scope migration and validation checks."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module
from app.config import settings
from app.routers.query import _scope_mode_of, _stored_document_ids
from app.schemas import MAX_SQLITE_ID, QueryBody


def main() -> None:
    body = QueryBody(question="测试问题", document_ids=[3, 2, 3])
    assert body.document_ids == [2, 3]
    assert QueryBody(question="测试问题", conversation_id=MAX_SQLITE_ID,
                     document_ids=[MAX_SQLITE_ID]).document_ids == [MAX_SQLITE_ID]
    for field in (
        {"conversation_id": True}, {"conversation_id": 1.0}, {"conversation_id": "1"},
        {"conversation_id": 2**63},
        {"document_ids": [True]}, {"document_ids": [1.0]}, {"document_ids": ["1"]},
        {"document_ids": [2**63]},
    ):
        try:
            QueryBody(question="测试问题", **field)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"non-integer scope ID accepted: {field}")
    assert _stored_document_ids("[3,2,3]") == [2, 3]
    assert _stored_document_ids(f"[{MAX_SQLITE_ID}]") == [MAX_SQLITE_ID]
    for raw in ("[true]", "[1.0]", '["1"]', "[0]", f"[{2**63}]", "{}", "not-json"):
        try:
            _stored_document_ids(raw)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid stored scope ID accepted: {raw}")

    # ---- 显式范围模式：全库检索 all / 仅选中文档 selected ----
    assert QueryBody(question="测试问题", scope_mode="all").document_ids is None
    assert QueryBody(question="测试问题", scope_mode="all", document_ids=[]).document_ids == []
    assert QueryBody(question="测试问题", scope_mode="selected", document_ids=[3, 3, 2]).document_ids == [2, 3]
    # 追问的范围已固化在对话上：允许只带 conversation_id + 相同的 scope_mode。
    follow_up = QueryBody(question="测试问题", conversation_id=7, scope_mode="selected")
    assert follow_up.document_ids is None and follow_up.scope_mode == "selected"
    legacy = QueryBody(question="测试问题", document_ids=[])
    assert legacy.scope_mode is None and legacy.document_ids == []
    assert _scope_mode_of([]) == "all"
    assert _scope_mode_of([2]) == "selected"

    invalid_scope_bodies = (
        ({"scope_mode": "all", "document_ids": [2]}, "全库模式带限定文档"),
        ({"scope_mode": "selected"}, "新对话仅选中文档缺 document_ids"),
        ({"scope_mode": "selected", "document_ids": []}, "新对话仅选中文档零勾选"),
        ({"scope_mode": "bogus"}, "未知模式"),
    )
    for field, why in invalid_scope_bodies:
        try:
            QueryBody(question="测试问题", **field)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid scope semantics accepted: {why}")
    # 零勾选必须明确说明"不会回退全库"，而不是静默接受空列表。
    try:
        QueryBody(question="测试问题", scope_mode="selected", document_ids=[])
    except ValidationError as exc:
        assert "至少选择一个文档" in str(exc)
        assert "全库" in str(exc)
    else:
        raise AssertionError("zero-selection selected scope must be rejected")

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

    print("Conversation scope check: PASS (validation, scope modes, legacy migration, idempotency)")


if __name__ == "__main__":
    main()
