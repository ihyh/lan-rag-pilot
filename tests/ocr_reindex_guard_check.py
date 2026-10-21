"""OCR 导入文档不得走普通重建：守卫拒绝、chunk/tags 保持不变。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(condition: bool, message: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {message}")
    else:
        FAIL += 1
        print(f"  [FAIL] {message}")


def main() -> int:
    temp_root = ROOT.parent / ".tmp-tests"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ocr-reindex-guard-", dir=temp_root) as temp:
        base = Path(temp)
        os.environ["RAG_DATA_DIR"] = str(base / "data")
        os.environ["RAG_DB_PATH"] = str(base / "data" / "rag.db")
        os.environ["RAG_UPLOAD_DIR"] = str(base / "data" / "uploads")
        os.environ["RAG_EMBED_BACKEND"] = "mock"
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from app import db as dbmod  # noqa: PLC0415
        from app.config import settings  # noqa: PLC0415
        from app.ingest import IngestError, _is_ocr_imported, reindex_document  # noqa: PLC0415

        dbmod.init_db()
        settings.ensure_dirs()
        (settings.upload_dir / "dummy.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        conn = dbmod.connect()
        now = dbmod.now_iso()
        tags = ["ocr", "ocr_ok_pages=1", "ocr_suspect_pages=2", "ocr_failed_pages=3"]
        cursor = conn.execute(
            "INSERT INTO documents (filename, stored_name, content_type, size_bytes, sha256,"
            " status, num_chunks, pages, tags, created_at, updated_at)"
            " VALUES (?,?,?,?,?,'ready',?,?,?,?,?)",
            ("dummy.pdf", "dummy.pdf", "application/pdf", 14, "a" * 64,
             1, 3, __import__("json").dumps(tags), now, now),
        )
        doc_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO chunks (document_id, seq, page, paragraph, token_count, content, vector)"
            " VALUES (?,?,?,?,?,?,?)",
            (doc_id, 0, 1, None, 2, "第一页", b"\x00" * 8),
        )
        before = conn.execute(
            "SELECT status, tags, num_chunks FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        chunk_before = conn.execute(
            "SELECT seq, page, content FROM chunks WHERE document_id=? ORDER BY seq", (doc_id,)
        ).fetchall()

        blocked = False
        try:
            reindex_document(conn, doc_id)
        except IngestError as exc:
            blocked = exc.code == "ocr_reindex_blocked" and exc.status_code == 409
        after = conn.execute(
            "SELECT status, tags, num_chunks FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        chunk_after = conn.execute(
            "SELECT seq, page, content FROM chunks WHERE document_id=? ORDER BY seq", (doc_id,)
        ).fetchall()
        check(blocked, "OCR 导入文档普通重建被明确拒绝（ocr_reindex_blocked/409）")
        check(dict(after) == dict(before), "拒绝后文档 status/tags/num_chunks 不变")
        check([tuple(row) for row in chunk_after] == [tuple(row) for row in chunk_before],
              "拒绝后 chunk 页码与正文不变（failed 页没有被重新加入）")
        page_values = sorted(row["page"] for row in chunk_after)
        check(page_values == [1], "当前索引仍只含 page 1，与 OCR failed 状态一致")
        check(_is_ocr_imported(before), "OCR tags 被识别")
        plain = dict(before)
        plain["tags"] = '["普通文档"]'
        check(not _is_ocr_imported(plain), "普通文档不被误判为 OCR 导入")
        conn.close()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
