"""资料体检与字面原文检索；只读，不调用嵌入或生成模型。"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .. import acl, parse_runner
from ..devices import COMMON_DEVICE, DEVICES, normalize_device
from ..source_metadata import SOURCE_TYPES, document_source_type, parse_metadata, source_type_sql
from ..config import settings
from ..db import get_db
from ..deps import require_kb_admin
from ..schemas import MAX_SQLITE_ID

router = APIRouter(prefix="/library")
_pdf_gate = threading.BoundedSemaphore(1)
_NONEMPTY = "length(trim(c.content, char(9)||char(10)||char(13)||' ')) > 0"
_CHUNK_COLUMNS = (
    "c.id, c.document_id, c.seq, c.page, c.paragraph, c.line_start, c.line_end, c.token_count, c.content, "
    "d.filename, d.version, d.status, d.stored_name, d.lifecycle, d.applicability, d.replacement_document_id,d.sha256,d.source_metadata,d.content_type,d.device"
)


class SearchBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    # Unlike legacy query requests, [] explicitly means no documents.
    document_ids: list[StrictInt] | None = Field(default=None, max_length=100)
    limit: StrictInt = Field(default=20, ge=1, le=50)
    offset: StrictInt = Field(default=0, ge=0, le=MAX_SQLITE_ID)
    page: StrictInt | None = Field(default=None, ge=1, le=MAX_SQLITE_ID)
    include_archived: bool = False
    source_types: list[str] = Field(default_factory=list, max_length=4)
    device: str | None = Field(default=None, max_length=32)

    @field_validator("device")
    @classmethod
    def validate_device(cls, value):
        if value is None or value == "":
            return None
        return normalize_device(value)

    @field_validator("source_types")
    @classmethod
    def validate_source_types(cls, values):
        cleaned = sorted({str(value).strip().lower() for value in values})
        if any(value not in SOURCE_TYPES for value in cleaned):
            raise ValueError("资料类型只能是 file、web、image 或 log")
        return cleaned

    @field_validator("document_ids")
    @classmethod
    def validate_ids(cls, values):
        if values is not None and any(not 0 < value <= MAX_SQLITE_ID for value in values):
            raise ValueError("文档 ID 必须在 SQLite 正整数范围内")
        return sorted(set(values)) if values is not None else None


def _source_file(document) -> Path | None:
    root = settings.upload_dir.resolve()
    path = (root / document["stored_name"]).resolve()
    return path if path.parent == root and path.is_file() else None


def _item(row) -> dict:
    return {
        key: row[key]
        for key in ("id", "document_id", "seq", "page", "paragraph", "line_start", "line_end", "token_count",
                    "content", "filename", "version", "lifecycle", "applicability", "replacement_document_id")
    } | {
        "chunk_id": row["id"], "chunk_index": row["seq"] + 1,
        "sha256":row['sha256'],"chunk_content_sha256":hashlib.sha256(row['content'].encode('utf-8')).hexdigest(),
        "can_open_file": _source_file(row) is not None,
        "source_metadata": parse_metadata(row['source_metadata']),
        "source_type": document_source_type(row['source_metadata'], row['content_type'], row['filename']),
        "device": row['device'],
    }


def _ocr_pages(raw: str) -> dict:
    result = {"ok": [], "suspect": [], "failed": []}
    try:
        tags = json.loads(raw)
    except (ValueError, TypeError):
        return result
    if not isinstance(tags, list):
        return result
    for tag in tags:
        match = re.fullmatch(r"ocr_(ok|suspect|failed)_pages=(.*)", tag.strip()) if isinstance(tag, str) else None
        if not match:
            continue
        for token in match[2].split(","):
            token = token.strip()
            if re.fullmatch(r"[0-9]{1,16}", token) and 0 < int(token) <= 2**53 - 1:
                result[match[1]].append(int(token))
    return {key: sorted(set(pages)) for key, pages in result.items()}


def _document_tags(document) -> list:
    try:
        tags = json.loads(document['tags'] or '[]')
    except (TypeError, ValueError):
        return []
    return tags if isinstance(tags, list) else []


def _direct_image_description(document) -> bool:
    tags = _document_tags(document)
    return ('image_text_direct' in tags and 'image_caption_only' in tags
            and document_source_type(document['source_metadata'], document['content_type'], document['filename']) == 'image')


def _pdf_count_child(conn, path: str, memory_mb: int, size_limit: int) -> None:
    """Only page-tree counting runs in the existing timeout/crash isolation."""
    try:
        parse_runner._limit_address_space(memory_mb)
        from pypdf import PdfReader

        if Path(path).stat().st_size > size_limit:
            raise ValueError("PDF exceeds inspection size limit")
        with open(path, "rb") as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("Encrypted PDF")
            count = len(reader.pages)
        conn.send(count if 0 < count <= 50000 else None)
    except Exception:
        conn.send(None)
    finally:
        conn.close()


def _pdf_summary(document, indexed_pages: list[int], requested: bool) -> dict:
    summary = {"status": "not_requested", "page_count": None,
               "pages_without_indexed_text": [], "note": "尚未核对原 PDF 的物理页数；入库记录页数不代表全文完整。"}
    if Path(document["filename"]).suffix.lower() != ".pdf":
        return summary | {"status": "not_pdf", "note": "该格式没有可靠的物理页码；请按段落与原文件核对。"}
    if not requested:
        return summary
    path = _source_file(document)
    if path is None:
        return summary | {"status": "missing_file", "note": "原文件不存在，无法核对 PDF 页数。"}
    size_limit = min(settings.max_upload_bytes, 20 * 1024 * 1024)
    if path.stat().st_size > size_limit:
        return summary | {"status": "too_large", "note": "文件超过本次在线页数核对上限（20 MB），物理页数未知。"}
    if not _pdf_gate.acquire(blocking=False):
        return summary | {"status": "busy", "note": "另一个 PDF 正在核对，请稍后重试。"}
    try:
        outcome = parse_runner._run_isolated(
            _pdf_count_child, (str(path), settings.parse_memory_mb, size_limit),
            min(5.0, settings.parse_timeout_s), name="library-pdf-pages",
        )
    finally:
        _pdf_gate.release()
    if not outcome.got or type(outcome.result) is not int:
        status = outcome.reason if not outcome.got else "failed"
        return summary | {"status": status, "note": "本次未能完成 PDF 页数核对，物理页数仍未知；未回退到主进程解析。"}
    pages = set(indexed_pages)
    return {"status": "ok", "page_count": outcome.result,
            "pages_without_indexed_text": [page for page in range(1, outcome.result + 1) if page not in pages],
            "note": "无索引文本的页可能是空白页、图片页或未提取页；有文本也不代表识别完整、表格正确。"}


@router.get("/inspection-summary")
def inspection_summary(db: sqlite3.Connection = Depends(get_db), user=Depends(require_kb_admin)):
    """汇总可见资料的基础索引状态；不代表原文完整或内容正确。"""
    allowed = acl.visible_document_ids(db, user)
    rows = db.execute(
        "SELECT d.*, j.state AS ocr_state, COUNT(c.id) AS actual_chunks, "
        f"COALESCE(SUM(CASE WHEN c.id IS NOT NULL AND NOT ({_NONEMPTY}) THEN 1 ELSE 0 END),0) AS empty_chunks, "
        "COALESCE(SUM(CASE WHEN c.id IS NOT NULL AND COALESCE(c.page,0)<=0 AND COALESCE(c.paragraph,0)<=0 "
        "AND NOT (COALESCE(c.line_start,0)>0 AND COALESCE(c.line_end,0)>=c.line_start) THEN 1 ELSE 0 END),0) AS missing_locations "
        "FROM documents d LEFT JOIN chunks c ON c.document_id=d.id LEFT JOIN ocr_jobs j ON j.document_id=d.id "
        "GROUP BY d.id ORDER BY d.filename COLLATE NOCASE,d.id"
    )
    issues = []
    total = 0
    for row in rows:
        if allowed is not None and row['id'] not in allowed:
            continue
        total += 1
        reasons = []
        if row['status'] != 'ready':
            reasons.append('处理未就绪')
        if not row['actual_chunks']:
            reasons.append('没有入库片段')
        if row['actual_chunks'] != row['num_chunks']:
            reasons.append('记录片段数与实际不一致')
        if row['empty_chunks']:
            reasons.append('存在空白片段')
        if row['missing_locations'] and not _direct_image_description(row):
            reasons.append('部分片段缺少位置')
        ocr = _ocr_pages(row['tags'])
        if ocr['suspect']:
            reasons.append('OCR 存在可疑页')
        if ocr['failed']:
            reasons.append('OCR 存在失败页')
        if row['ocr_state'] == 'review' or 'ocr_human_review_pending' in _document_tags(row):
            reasons.append('OCR 文字待核对')
        if row['ocr_state'] == 'failed' and not ocr['failed']:
            reasons.append('OCR 处理失败')
        metadata = parse_metadata(row['source_metadata'])
        if metadata and metadata['kind'] == 'web' and metadata['status'] == 'partial':
            reasons.append('网页内容未完全提取或核对')
        if reasons:
            issues.append({'id': row['id'], 'filename': row['filename'], 'lifecycle': row['lifecycle'], 'reasons': reasons})
    return {'total': total, 'items': issues}


@router.get("/documents/{document_id}/inspection")
def inspect_document(
    document_id: int = PathParam(ge=1, le=MAX_SQLITE_ID),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=MAX_SQLITE_ID),
    page: int | None = Query(default=None, ge=1, le=MAX_SQLITE_ID),
    check_pdf: bool = False,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    if not acl.can_access(db, user, document_id):
        raise HTTPException(404, "文档不存在")
    doc = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
    if doc is None:
        raise HTTPException(404, "文档不存在")
    stats = dict(db.execute(
        "SELECT COUNT(*) AS chunk_count, COALESCE(SUM(length(c.content)),0) AS text_chars, "
        f"COALESCE(SUM(NOT ({_NONEMPTY})),0) AS empty_chunk_count, "
        "COUNT(DISTINCT CASE WHEN c.paragraph>0 THEN c.paragraph END) AS paragraph_count, "
        "COALESCE(SUM(COALESCE(c.page,0)<=0 AND COALESCE(c.paragraph,0)<=0 AND "
        "NOT (COALESCE(c.line_start,0)>0 AND COALESCE(c.line_end,0)>=c.line_start)),0) AS chunks_without_location "
        "FROM chunks c WHERE document_id=?", (document_id,),
    ).fetchone())
    indexed_pages = [row[0] for row in db.execute(
        f"SELECT DISTINCT c.page FROM chunks c WHERE c.document_id=? AND c.page>0 AND {_NONEMPTY} ORDER BY c.page",
        (document_id,),
    )]
    ocr = _ocr_pages(doc["tags"])
    warnings = ["就绪仅表示处理流程完成，不保证正文读全或表格解析正确；请抽查下面的实际入库文本。"]
    if doc["status"] != "ready":
        warnings.append("该文档当前未就绪，不参与原文检索和问答。")
    if stats["chunk_count"] == 0:
        warnings.append("当前没有入库片段，无法据此回答问题。")
    if stats["chunk_count"] != doc["num_chunks"]:
        warnings.append("记录片段数与实际索引片段数不一致，请核对入库状态。")
    if stats["empty_chunk_count"]:
        warnings.append("索引中存在空白片段。")
    if stats["chunks_without_location"] and not _direct_image_description(doc):
        warnings.append("部分片段缺少页码、段落或日志行号位置。")
    if ocr["suspect"]:
        warnings.append("OCR 标记了可疑页，需要人工核对对应页的索引正文与识别文本。")
    if ocr["failed"]:
        warnings.append("OCR 标记了失败页，请核对原文件；这些页按导入规则不应进入索引。")
    if set(ocr["failed"]) & set(indexed_pages):
        warnings.append("发现 OCR 失败页仍有索引文本，标签与索引不一致。")
    pdf = _pdf_summary(doc, indexed_pages, check_pdf)
    metadata = parse_metadata(doc['source_metadata'])
    if metadata and metadata['kind']=='web':
        pdf['note'] = '网页使用提取块号，不是浏览器页码或原文段落号。'
        if metadata['status'] == 'partial':
            warnings.append('网页含未提取或未核对的内容；图片文字未从网页正文提取，可查看单独入库的图片或对照原网页。')
    if metadata and metadata['kind']=='log':
        pdf['note'] = '日志按原件CRLF/LF/CR物理行号定位，不是PDF页码或普通段落号。'
    if pdf["page_count"] is not None and any(number > pdf["page_count"] for number in indexed_pages):
        warnings.append("部分索引页码超出当前原 PDF 的物理页数，请核对来源文件。")
    where, params = "c.document_id=?", [document_id]
    if page is not None:
        where += " AND c.page=?"
        params.append(page)
    total = db.execute(f"SELECT COUNT(*) FROM chunks c WHERE {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT {_CHUNK_COLUMNS} FROM chunks c JOIN documents d ON d.id=c.document_id "
        f"WHERE {where} ORDER BY c.seq,c.id LIMIT ? OFFSET ?", (*params, limit, offset),
    )
    return {
        "document": {key: doc[key] for key in ("id", "filename", "version", "status", "size_bytes", "tags", "updated_at", "lifecycle", "replacement_document_id", "applicability", "device")}
        | {"error_message": doc["error"], "stored_page_count": doc["pages"], "source_metadata": metadata,
           "source_type": document_source_type(doc['source_metadata'], doc['content_type'], doc['filename']),
           "direct_image_description": _direct_image_description(doc)},
        "quality": stats | {"recorded_chunk_count": doc["num_chunks"], "indexed_pages": indexed_pages,
                            "ocr": ocr, "pdf": pdf, "warnings": warnings},
        "items": [_item(row) for row in rows], "total": total, "limit": limit, "offset": offset,
        "can_open_file": _source_file(doc) is not None,
    }


@router.post("/search")
def search_text(
    body: SearchBody,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    result = {"query": body.query, "terms": [], "total": 0, "limit": body.limit, "offset": body.offset,
              "items": [], "groups": []}
    if body.document_ids == []:
        return result
    terms = [term[:50] for term in re.split(r"\s+", body.query.strip()) if term][:8]
    if not terms:
        return result
    result["terms"] = terms
    allowed = acl.visible_document_ids(db, user)
    requested = set(body.document_ids) if body.document_ids is not None else None
    if requested is not None:
        if allowed is not None and not requested <= allowed:
            raise HTTPException(404, "限定文档不存在")
        marks = ",".join("?" for _ in requested)
        ready = {row[0] for row in db.execute(
            f"SELECT id FROM documents WHERE status='ready' AND id IN ({marks})", sorted(requested),
        )}
        if ready != requested:
            raise HTTPException(400, "所选文档已删除或未就绪，请重新选择；不会回退全库检索")
        if not body.include_archived:
            historical = db.execute(f"SELECT id FROM documents WHERE lifecycle!='active' AND id IN ({marks})", sorted(requested)).fetchone()
            if historical is not None:
                raise HTTPException(400, '所选资料为历史版本；如需核对，请显式勾选“包含历史版本”')
    scope = requested if requested is not None else allowed
    if scope == set():
        return result
    # 每个关键词都要出现（AND）；instr 按字面匹配，lower 提供 ASCII 大小写不敏感。
    where = "d.status='ready'"
    params: list = []
    for term in terms:
        where += " AND instr(lower(c.content),lower(?))>0"
        params.append(term)
    if not body.include_archived:
        where += " AND d.lifecycle='active'"
    if scope is not None:
        where += " AND d.id IN (" + ",".join("?" for _ in scope) + ")"
        params.extend(sorted(scope))
    if body.page is not None:
        where += " AND c.page=?"
        params.append(body.page)
    if body.source_types:
        where += " AND (" + " OR ".join(source_type_sql(kind) for kind in body.source_types) + ")"
    if body.device:
        if body.device == COMMON_DEVICE:
            where += " AND d.device=?"
            params.append(body.device)
        else:
            where += " AND d.device IN (?,?)"
            params.extend((body.device, COMMON_DEVICE))
    source = "FROM chunks c JOIN documents d ON d.id=c.document_id "
    result["total"] = db.execute(f"SELECT COUNT(*) {source} WHERE {where}", params).fetchone()[0]
    result["items"] = [_item(row) for row in db.execute(
        f"SELECT {_CHUNK_COLUMNS} {source} WHERE {where} "
        "ORDER BY d.filename COLLATE NOCASE,d.id,c.seq,c.id LIMIT ? OFFSET ?",
        (*params, body.limit, body.offset),
    )]
    result["groups"] = [
        {"document_id": row["document_id"], "filename": row["filename"], "hits": row["hits"], "device": row["device"],
         "source_type": document_source_type(row["source_metadata"], row["content_type"], row["filename"])}
        for row in db.execute(
            f"SELECT d.id AS document_id, d.filename, d.content_type, d.source_metadata, d.device, COUNT(*) AS hits "
            f"{source} WHERE {where} GROUP BY d.id ORDER BY hits DESC, d.filename COLLATE NOCASE",
            params,
        )
    ]
    return result
