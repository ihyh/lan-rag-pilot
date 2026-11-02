"""把已核对的 OCR 输出导入隔离 RAG 测试库，并核对正文/引用/页码。

这是**集成验证工具**，不接入上传 API；只允许写入工作区内 `.tmp-tests` 下的隔离
DB/上传目录，绝不指向生产库。原始 PDF 仍保存为来源文件；OCR 页文本按真实页码切块。
suspect 页可索引但会在文档 tags/审计报告里可见；failed 页不进入索引并明确列出。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISOLATED_ROOT = ROOT.parent / ".tmp-tests"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _page_list(values: list[int]) -> str:
    return ",".join(str(value) for value in sorted(set(values))) or "-"


def _physical_pdf_pages(data: bytes) -> int:
    import io  # noqa: PLC0415

    from pypdf import PdfReader  # noqa: PLC0415

    try:
        count = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"无法读取原始 PDF 页数，拒绝导入：{exc}") from exc
    if count <= 0:
        raise SystemExit("原始 PDF 没有物理页，拒绝导入")
    return count


def validate_ocr_pages(result: dict, physical_pages: int) -> dict:
    """页码必须为正整数、唯一、覆盖全部物理页；状态只允许 ok/suspect/failed。"""
    records = result.get("pages")
    if not isinstance(records, list) or not records:
        raise SystemExit("OCR 结果没有 pages，拒绝导入")
    allowed = {"ok", "suspect", "failed"}
    pages: dict[int, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit("OCR pages 记录格式错误，拒绝导入")
        page_no = record.get("page")
        if type(page_no) is not int or page_no <= 0:
            raise SystemExit(f"OCR 页码必须是正整数，实际 {page_no!r}")
        if page_no in pages:
            raise SystemExit(f"OCR 页码重复：{page_no}，拒绝合并/覆盖")
        status = record.get("status") or "ok"
        if status not in allowed:
            raise SystemExit(f"OCR 页 {page_no} 状态 {status!r} 不在允许集合 {sorted(allowed)}")
        if page_no > physical_pages:
            raise SystemExit(f"OCR 页码 {page_no} 超出原 PDF 物理页数 {physical_pages}")
        pages[page_no] = record
    missing = set(range(1, physical_pages + 1)) - set(pages)
    if missing:
        raise SystemExit(f"OCR 结果缺少物理页 {sorted(missing)}，拒绝把不完整结果当完整导入")
    return pages


def load_inputs(ocr_json: Path, source: Path) -> tuple[dict, dict, dict]:
    result = json.loads(ocr_json.read_text(encoding="utf-8"))
    data = source.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    meta = result.get("source") or {}
    expected_sha = meta.get("sha256")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise SystemExit("OCR 结果必须提供有效的 source.sha256（64 位十六进制），拒绝建立来源")
    if expected_sha.lower() != actual_sha:
        raise SystemExit("OCR 结果的 source.sha256 与原始文件不一致，拒绝导入")
    pages = validate_ocr_pages(result, _physical_pdf_pages(data))
    return result, {"sha256": actual_sha, "size_bytes": len(data)}, pages


def _resolve(path: Path) -> Path:
    return path.resolve()


def preflight_paths(args) -> None:
    """任何写入前检查路径别名：报告必须是新文件，不能覆盖原件/OCR/DB/旧报告。"""
    source = _resolve(args.source)
    ocr_json = _resolve(args.ocr_json)
    db = _resolve(args.db)
    report = _resolve(args.report)
    upload = _resolve(args.upload_dir)
    if not source.is_file():
        raise SystemExit(f"原始文件不存在：{args.source}")
    if not ocr_json.is_file():
        raise SystemExit(f"OCR JSON 不存在：{args.ocr_json}")
    if not _is_under(db, ISOLATED_ROOT) or not _is_under(upload, ISOLATED_ROOT):
        raise SystemExit(f"只允许在隔离目录 {ISOLATED_ROOT} 下写入测试 DB/上传目录")
    if report.exists() or report.is_symlink():
        raise SystemExit(f"--report 必须是新文件，拒绝覆盖：{args.report}")
    if report.is_dir():
        raise SystemExit(f"--report 不能是目录：{args.report}")
    blocked = {
        "原件": source, "OCR JSON": ocr_json, "DB": db, "上传目录": upload,
    }
    for name, target in blocked.items():
        if report == target:
            raise SystemExit(f"--report 不能与{name}相同：{args.report}")
    if db in {source, ocr_json}:
        raise SystemExit("--db 不能与原件/OCR JSON 相同")
    if upload == source or upload == ocr_json or upload == db:
        raise SystemExit("--upload-dir 不能与原件/OCR JSON/DB 相同")
    if not source.suffix.lower() == ".pdf":
        raise SystemExit("当前集成验证只接受 PDF 原件；图片/TIFF 未纳入本轮")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-json", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--upload-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--question", action="append", default=[],
                        help="导入后验证检索的问题，可重复")
    parser.add_argument("--expect-page", action="append", type=int, default=[],
                        help="与 --question 按顺序对应：期望来源页码")
    parser.add_argument("--expect-token", action="append", default=[],
                        help="与 --question 按顺序对应：来源正文应包含的关键字")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--embedding-backend", choices=("st", "mock"), default="st")
    parser.add_argument("--embedding-model", default="")
    args = parser.parse_args()
    if len(args.expect_page) not in (0, len(args.question)) or len(args.expect_token) not in (0, len(args.question)):
        raise SystemExit("--expect-page/--expect-token 必须与 --question 数量一致或省略")
    preflight_paths(args)
    result, source_meta, pages = load_inputs(args.ocr_json, args.source)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report_handle = args.report.open("x", encoding="utf-8")
    try:
        return _run_import(args, result, source_meta, pages, report_handle)
    except BaseException:
        report_handle.close()
        raise


def _run_import(args, result: dict, source_meta: dict, pages: dict[int, dict], report_handle) -> int:
    args.upload_dir.mkdir(parents=True, exist_ok=True)
    os.environ["RAG_DATA_DIR"] = str(args.db.parent / "rag-data")
    os.environ["RAG_DB_PATH"] = str(args.db)
    os.environ["RAG_UPLOAD_DIR"] = str(args.upload_dir)
    os.environ["RAG_EMBED_BACKEND"] = args.embedding_backend
    if args.embedding_model:
        os.environ["RAG_EMBED_MODEL"] = args.embedding_model
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from app import db as dbmod  # noqa: PLC0415
    from app.chunking import TokenizerAdapter, chunk_units  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415
    from app.embeddings import embedding_service  # noqa: PLC0415
    from app.index import vector_index  # noqa: PLC0415
    from app.parsing import Unit  # noqa: PLC0415

    dbmod.init_db()
    page_records = [pages[page_no] for page_no in sorted(pages)]
    statuses = {int(page["page"]): str(page.get("status") or "ok") for page in page_records}
    warnings = {int(page["page"]): list(page.get("warnings") or []) for page in page_records}
    units: list[Unit] = []
    imported_pages: list[int] = []
    skipped_failed: list[int] = []
    skipped_empty: list[int] = []
    for page in page_records:
        page_no = int(page["page"])
        text = (page.get("text") or "").strip()
        status = statuses[page_no]
        if status == "failed":
            skipped_failed.append(page_no)
            continue
        if not text:
            skipped_empty.append(page_no)
            continue
        units.append(Unit(text=text, page=page_no))
        imported_pages.append(page_no)
    if not units:
        raise SystemExit("没有可导入的 ok/suspect 页文本")

    if embedding_service.state != "ready":
        if args.embedding_backend == "mock":
            embedding_service.start()
        else:
            embedding_service._load_real()
    if embedding_service.state != "ready":
        raise SystemExit(f"嵌入服务不可用：{embedding_service.message}")

    pieces = chunk_units(
        units, TokenizerAdapter(embedding_service.tokenizer_or_none()),
        settings.chunk_max_tokens, settings.chunk_overlap_tokens,
        max_pieces=settings.parse_max_chunks,
    )
    if not pieces:
        raise SystemExit("OCR 文本没有产生可索引切片")
    vectors = embedding_service.embed_texts([piece.text for piece in pieces])

    stored_name = f"{uuid.uuid4().hex}{args.source.suffix.lower()}"
    source_copy = args.upload_dir / stored_name
    source_copy.write_bytes(args.source.read_bytes())
    stored_sha = hashlib.sha256(source_copy.read_bytes()).hexdigest()
    if stored_sha != source_meta["sha256"]:
        source_copy.unlink(missing_ok=True)
        raise SystemExit("复制原始 PDF 后 SHA-256 不一致（原件在导入期间被改动），拒绝入库")
    tags = ["ocr"]
    for status_name in ("ok", "suspect", "failed"):
        status_pages = [page_no for page_no, status in statuses.items() if status == status_name]
        if status_pages:
            tags.append(f"ocr_{status_name}_pages={_page_list(status_pages)}")

    conn = dbmod.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "INSERT INTO documents (filename, stored_name, content_type, size_bytes, sha256,"
            " status, error, num_chunks, pages, version, effective_date, tags, visibility,"
            " uploaded_by, created_at, updated_at) VALUES (?,?,?,?,?,'ready',NULL,?,?,?,?,?,?,NULL,?,?)",
            (
                args.source.name, stored_name, "application/pdf", source_meta["size_bytes"],
                source_meta["sha256"], len(pieces), len(page_records), "ocr-import",
                None, json.dumps(tags, ensure_ascii=False), "shared",
                dbmod.now_iso(), dbmod.now_iso(),
            ),
        )
        doc_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO chunks (document_id, seq, page, paragraph, token_count, content, vector)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (doc_id, index, piece.page, piece.paragraph, piece.token_count,
                 piece.text, vectors[index].tobytes())
                for index, piece in enumerate(pieces)
            ],
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        source_copy.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    conn = dbmod.connect()
    try:
        vector_index.reload(conn)
        from app.routers.query import retrieve_sources  # noqa: PLC0415

        checks = []
        for index, question in enumerate(args.question):
            sources, retrieval_ms, empty_reason = retrieve_sources(
                conn, question, {doc_id}, args.top_k
            )
            expected_page = args.expect_page[index] if args.expect_page else None
            expected_token = args.expect_token[index] if args.expect_token else None
            source_checks = []
            for source in sources:
                row = conn.execute(
                    "SELECT c.content, c.page, c.document_id, d.filename FROM chunks c"
                    " JOIN documents d ON d.id=c.document_id WHERE c.id=?",
                    (source["chunk_id"],),
                ).fetchone()
                source_doc = source.get("document_id")
                db_doc = int(row["document_id"]) if row else None
                page_ok = expected_page is None or source.get("page") == expected_page
                token_ok = expected_token is None or expected_token in (source.get("content") or "")
                document_ok = source_doc == doc_id and db_doc == doc_id
                source_checks.append({
                    "chunk_id": source["chunk_id"],
                    "document_id": source_doc,
                    "page": source.get("page"),
                    "filename": source.get("filename"),
                    "content_in_db": bool(row and row["content"] == source.get("content")),
                    "page_in_db": bool(row and row["page"] == source.get("page")),
                    "filename_in_db": bool(row and row["filename"] == source.get("filename")),
                    "document_in_db": document_ok,
                    "page_ok": page_ok,
                    "token_ok": token_ok,
                    "same_source_match": document_ok and page_ok and token_ok,
                    "failed_page": statuses.get(int(source.get("page") or 0)) == "failed",
                })
            page_hit = any(item["page_ok"] for item in source_checks) if expected_page is not None else True
            token_hit = any(item["token_ok"] for item in source_checks) if expected_token is not None else True
            expectation_hit = (
                any(item["same_source_match"] for item in source_checks)
                if expected_page is not None or expected_token is not None else True
            )
            checks.append({
                "question": question,
                "retrieval_ms": round(retrieval_ms, 2),
                "empty_reason": empty_reason,
                "expected_page": expected_page,
                "expected_token": expected_token,
                "page_hit": page_hit,
                "token_hit": token_hit,
                "expectation_hit": expectation_hit,
                "sources": source_checks,
            })
        report = {
            "source": {
                "ocr_json": str(args.ocr_json.resolve()),
                "original_filename": args.source.name,
                "sha256": source_meta["sha256"],
                "sha256_matches_ocr": True,
                "pdf_pages": len(pages),
                "document_id": doc_id,
                "stored_name": stored_name,
                "stored_path": str(source_copy.resolve()),
                "stored_sha256": stored_sha,
                "stored_sha256_matches": stored_sha == source_meta["sha256"],
            },
            "pages": {
                "total": len(page_records),
                "statuses": {str(page): status for page, status in sorted(statuses.items())},
                "warnings": {str(page): items for page, items in sorted(warnings.items())},
                "imported": imported_pages,
                "skipped_failed": skipped_failed,
                "skipped_empty": skipped_empty,
                "suspect": sorted(page for page, status in statuses.items() if status == "suspect"),
            },
            "chunks": [
                {"chunk_id": int(row["id"]), "seq": int(row["seq"]), "page": row["page"],
                 "paragraph": row["paragraph"], "content": row["content"]}
                for row in conn.execute(
                    "SELECT id, seq, page, paragraph, content FROM chunks"
                    " WHERE document_id=? ORDER BY seq", (doc_id,)
                )
            ],
            "tags": tags,
            "checks": checks,
        }
        report["failed_page_in_sources"] = any(
            item["failed_page"] for check in checks for item in check["sources"]
        )
        report["source_ok"] = (
            stored_sha == source_meta["sha256"]
            and Path(report["source"]["stored_path"]).is_file()
            and hashlib.sha256(args.source.read_bytes()).hexdigest() == source_meta["sha256"]
        )
        report["mapping_ok"] = all(
            item["content_in_db"] and item["page_in_db"]
            and item["filename_in_db"] and item["document_in_db"]
            for check in checks for item in check["sources"]
        )
        report["expectations_ok"] = all(
            check["expectation_hit"] for check in checks
        )
    finally:
        conn.close()

    report_handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report_handle.close()
    print(json.dumps({
        "document_id": doc_id,
        "pages": report["pages"],
        "mapping_ok": report["mapping_ok"],
        "failed_page_in_sources": report["failed_page_in_sources"],
        "expectations_ok": report["expectations_ok"],
        "source_ok": report["source_ok"],
        "report": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0 if (
        report["mapping_ok"] and not report["failed_page_in_sources"]
        and report["expectations_ok"] and report["source_ok"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
