"""文件入库编排：校验 → 持久化(UUID 存储) → 解析 → 切块 → 向量化 → 重建索引。

- SHA-256 相同的文件拒绝重复入库；
- 扩展名/MIME/魔数/实际解析 四层校验，解析失败明确报错（如扫描 PDF）；
- 入库全程串行（ingest_lock），避免 CPU 过载与索引重建竞争。
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from pathlib import Path

from . import parsing
from .chunking import TokenizerAdapter, chunk_units
from .config import settings
from .db import now_iso
from .embeddings import EmbeddingUnavailable, embedding_service
from .index import vector_index

ingest_lock = threading.RLock()


class IngestError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "ingest_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _fetch_doc(db: sqlite3.Connection, doc_id: int):
    return db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()


def doc_dict(row) -> dict:
    return dict(row)


def _fail_doc(db: sqlite3.Connection, doc_id: int, message: str) -> None:
    db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    db.execute(
        "UPDATE documents SET status='failed', error=?, num_chunks=0, updated_at=? WHERE id=?",
        (str(message)[:500], now_iso(), doc_id),
    )


def _ready_or_raise() -> None:
    if embedding_service.state != "ready":
        raise EmbeddingUnavailable(embedding_service.message or "嵌入模型尚未就绪，请稍后再试")


def ingest_bytes(
    db: sqlite3.Connection,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    user_id: int,
    version: str = "1.0",
    effective_date: str | None = None,
) -> dict:
    """校验并入库单个文件，返回入库后的文档 dict。"""
    if not data:
        raise IngestError("文件为空，无可索引内容", code="empty_file")
    if len(data) > settings.max_upload_bytes:
        raise IngestError(
            f"文件超过 {settings.max_upload_mb} MB 上限", status_code=413, code="too_large"
        )

    try:
        ext = parsing.detect_ext(filename)      # 扩展名白名单
        kind = parsing.EXTENSIONS[ext]
        parsing.check_mime(content_type)        # MIME 白名单（octet-stream 放行，靠魔数把关）
        parsing.check_magic(kind, data)         # 魔数校验
    except parsing.ParseError as exc:
        raise IngestError(exc.message, code=exc.code) from exc

    sha256 = hashlib.sha256(data).hexdigest()
    dup = db.execute(
        "SELECT id, filename, status FROM documents WHERE sha256=?", (sha256,)
    ).fetchone()
    if dup:
        raise IngestError(
            f"文件内容重复（SHA-256 相同）：已存在文档 #{dup['id']}《{dup['filename']}》"
            f"（状态：{dup['status']}），拒绝重复入库",
            status_code=409,
            code="duplicate",
        )

    _ready_or_raise()
    if vector_index.size() > 0 and embedding_service.dim != vector_index.dim():
        raise IngestError(
            f"向量维度不一致：新文档 {embedding_service.dim} 维，知识库现有 "
            f"{vector_index.dim()} 维（可能更换过嵌入模型/后端）。请先删除全部文档后重建。",
            status_code=409,
            code="dim_mismatch",
        )

    stored_name = f"{uuid.uuid4().hex}{ext}"
    upload_path = settings.upload_dir / stored_name
    upload_path.write_bytes(data)

    now = now_iso()
    cur = db.execute(
        "INSERT INTO documents (filename, stored_name, content_type, size_bytes, sha256, status,"
        " version, effective_date, uploaded_by, created_at, updated_at) VALUES (?,?,?,?,?,'parsing',?,?,?,?,?)",
        (
            filename,
            stored_name,
            content_type or "application/octet-stream",
            len(data),
            sha256,
            version,
            effective_date,
            user_id,
            now,
            now,
        ),
    )
    doc_id = int(cur.lastrowid)
    try:
        return _index_document(db, doc_id, kind)
    except parsing.ParseError as exc:
        _fail_doc(db, doc_id, exc.message)
        raise IngestError(exc.message, code=exc.code) from exc
    except EmbeddingUnavailable as exc:
        _fail_doc(db, doc_id, str(exc))
        raise
    except IngestError:
        _fail_doc(db, doc_id, "索引失败")
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_doc(db, doc_id, f"内部错误：{exc.__class__.__name__}")
        raise IngestError(
            "入库过程中发生内部错误，请重试（也可对该文档执行“重新索引”）",
            status_code=500,
            code="internal",
        ) from exc


def _index_document(db: sqlite3.Connection, doc_id: int, kind: str) -> dict:
    """假定文档行已存在且状态为 parsing，执行解析/切块/向量化并置为 ready。"""
    doc = _fetch_doc(db, doc_id)
    assert doc is not None
    units = parsing.PARSERS[kind](settings.upload_dir / doc["stored_name"])
    ta = TokenizerAdapter(embedding_service.tokenizer_or_none())
    pieces = chunk_units(units, ta, settings.chunk_max_tokens, settings.chunk_overlap_tokens)
    if not pieces:
        raise IngestError("解析完成但没有可切块的文本", code="empty_doc")
    vecs = embedding_service.embed_texts([p.text for p in pieces])
    rows = [
        (doc_id, i, p.page, p.paragraph, p.token_count, p.text, vecs[i].tobytes())
        for i, p in enumerate(pieces)
    ]
    db.executemany(
        "INSERT INTO chunks (document_id, seq, page, paragraph, token_count, content, vector)"
        " VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    pages = max((u.page or 0) for u in units) if kind == "pdf" else None
    db.execute(
        "UPDATE documents SET status='ready', error=NULL, num_chunks=?, pages=?, updated_at=? WHERE id=?",
        (len(pieces), pages, now_iso(), doc_id),
    )
    vector_index.reload(db)
    return doc_dict(_fetch_doc(db, doc_id))


def reindex_document(db: sqlite3.Connection, doc_id: int) -> dict:
    """删除旧切片后按原文件重新解析/切块/向量化。"""
    doc = _fetch_doc(db, doc_id)
    if doc is None:
        raise IngestError("文档不存在", status_code=404, code="not_found")
    _ready_or_raise()
    kind = parsing.EXTENSIONS[Path(doc["stored_name"]).suffix.lower()]
    db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    db.execute(
        "UPDATE documents SET status='parsing', error=NULL, num_chunks=0, updated_at=? WHERE id=?",
        (now_iso(), doc_id),
    )
    try:
        return _index_document(db, doc_id, kind)
    except parsing.ParseError as exc:
        _fail_doc(db, doc_id, exc.message)
        raise IngestError(exc.message, code=exc.code) from exc
    except EmbeddingUnavailable as exc:
        _fail_doc(db, doc_id, str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_doc(db, doc_id, f"内部错误：{exc.__class__.__name__}")
        raise IngestError(
            "重新索引失败，请稍后重试", status_code=500, code="internal"
        ) from exc


def delete_document(db: sqlite3.Connection, doc_id: int) -> dict:
    """删除文档及其切片与存储文件，并重建内存索引。"""
    doc = _fetch_doc(db, doc_id)
    if doc is None:
        raise IngestError("文档不存在", status_code=404, code="not_found")
    upload_path = settings.upload_dir / doc["stored_name"]
    db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    upload_path.unlink(missing_ok=True)
    vector_index.reload(db)
    return {"id": int(doc["id"]), "filename": doc["filename"]}
