"""日志专用入库：原件不改写，严格解码，事务替换物理行索引。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import audit, ingest, log_content
from .chunking import TokenizerAdapter
from .config import settings
from .db import now_iso
from .embeddings import embedding_service
from .index import vector_index


def _prepare(raw, encoding):
    try:
        metadata, lines = log_content.parse(raw, encoding,
            max_chars=min(settings.parse_max_text_chars,4_000_000),
            max_lines=min(settings.parse_max_units,50_000))
        pieces = log_content.build_pieces(lines,TokenizerAdapter(embedding_service.tokenizer_or_none()),
            settings.chunk_max_tokens,settings.chunk_overlap_tokens,settings.parse_max_chunks)
    except log_content.LogError as exc:
        raise ingest.IngestError(str(exc),422,'log_parse_failed') from exc
    return metadata,pieces,embedding_service.embed_texts([piece.text for piece in pieces])


def _save_chunks(db, doc_id, metadata, pieces, vectors):
    db.execute('DELETE FROM chunks WHERE document_id=?',(doc_id,))
    db.executemany('INSERT INTO chunks (document_id,seq,page,paragraph,line_start,line_end,token_count,content,vector) '
        'VALUES (?,?,NULL,NULL,?,?,?,?,?)',
        [(doc_id,i,piece.line_start,piece.line_end,piece.token_count,piece.text,vectors[i].tobytes())
         for i,piece in enumerate(pieces)])
    db.execute("UPDATE documents SET status='ready',error=NULL,num_chunks=?,pages=NULL,source_metadata=?,updated_at=? WHERE id=?",
        (len(pieces),json.dumps(metadata,ensure_ascii=False),now_iso(),doc_id))


def import_log(db, *, filename, content_type, raw, encoding, user, version='1.0', visibility='shared', ip=''):
    if Path(filename).suffix.lower() not in ('.log','.txt'):
        raise ingest.IngestError('日志模式仅支持LOG或TXT原件',422,'log_extension_invalid')
    if not isinstance(encoding,str) or encoding not in log_content.ENCODINGS:
        raise ingest.IngestError('请选择自动/BOM、UTF-8、UTF-16或GB18030；字节序仅从BOM读取',422,'log_encoding_invalid')
    if not raw or len(raw)>settings.max_upload_bytes:
        raise ingest.IngestError('日志为空或超过上传大小上限',413 if raw else 422,'log_size_invalid')
    if (content_type or '').split(';')[0].strip().lower() not in ('','text/plain','application/octet-stream'):
        raise ingest.IngestError('日志仅接受纯文本MIME',422,'log_mime_invalid')
    with ingest.ingest_lock:
        sha = hashlib.sha256(raw).hexdigest()
        if db.execute('SELECT 1 FROM documents WHERE sha256=?',(sha,)).fetchone():
            raise ingest.IngestError('相同日志原件已存在，不重复导入',409,'duplicate')
        ingest._ready_or_raise()
        metadata,pieces,vectors = _prepare(raw,encoding)
        actual = metadata['encoding']
        charset = 'utf-8' if actual=='utf-8-sig' else 'utf-16' if actual.startswith('utf-16') else actual
        doc = None
        db.execute('BEGIN IMMEDIATE')
        try:
            doc,_ = ingest.register_bytes(db,filename=filename,content_type='text/plain; charset='+charset,
                data=raw,user_id=user.id,version=version,visibility=visibility,tags=['log_source'],log_text=True)
            _save_chunks(db,doc['id'],metadata,pieces,vectors)
            audit.log_audit(db,action='doc_upload',user_id=user.id,username=user.username,
                detail=f"doc:{doc['id']} log sha256:{sha} lines:{metadata['line_count']} chunks:{len(pieces)}",ip=ip)
            vector_index.reload(db)
            db.commit()
        except BaseException:
            db.rollback()
            if doc:
                (settings.upload_dir/doc['stored_name']).unlink(missing_ok=True)
            vector_index.reload(db)
            raise
        return ingest.doc_dict(ingest._fetch_doc(db,doc['id']))


def reindex_document(db, doc, *, user=None, ip=''):
    metadata = log_content.metadata(doc['source_metadata'])
    if metadata is None or metadata['sha256']!=doc['sha256']:
        raise ingest.IngestError('日志来源元数据损坏，不能按普通文本重建',409,'log_metadata_invalid')
    root = settings.upload_dir.resolve()
    path = (root/doc['stored_name']).resolve()
    if path.parent!=root or not path.is_file():
        raise ingest.IngestError('日志原件不存在',409,'log_source_missing')
    with path.open('rb') as source:
        raw = source.read(settings.max_upload_bytes+1)
    if hashlib.sha256(raw).hexdigest()!=doc['sha256']:
        raise ingest.IngestError('日志原件SHA不一致，已保留原索引',409,'log_source_changed')
    ingest._ready_or_raise()
    updated,pieces,vectors = _prepare(raw,metadata['encoding'])
    if updated!=metadata:
        raise ingest.IngestError('日志编码或行号元数据与原件不一致，已保留原索引',409,'log_metadata_invalid')
    with path.open('rb') as source:
        if hashlib.sha256(source.read(settings.max_upload_bytes+1)).hexdigest()!=doc['sha256']:
            raise ingest.IngestError('日志原件在重建期间变化，已保留原索引',409,'log_source_changed')
    db.execute('BEGIN IMMEDIATE')
    try:
        current = ingest._fetch_doc(db,doc['id'])
        if current is None or current['sha256']!=doc['sha256'] or current['source_metadata']!=doc['source_metadata']:
            raise ingest.IngestError('日志记录在重建期间变化，请刷新后重试',409,'log_source_changed')
        _save_chunks(db,doc['id'],metadata,pieces,vectors)
        if user is not None:
            audit.log_audit(db,action='doc_reindex',user_id=user.id,username=user.username,
                detail=f"doc:{doc['id']} saved_log_original sha256:{doc['sha256']} chunks:{len(pieces)}",ip=ip)
        vector_index.reload(db)
        db.commit()
    except BaseException:
        db.rollback()
        vector_index.reload(db)
        raise
    return ingest.doc_dict(ingest._fetch_doc(db,doc['id']))
