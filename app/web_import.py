"""公开网页持久入库；复用文档登记、向量索引和来源契约。"""
from __future__ import annotations

import hashlib
import json
from http.client import HTTPException
from urllib.error import URLError

from . import audit, ingest, web_content, public_web
from .chunking import TokenizerAdapter
from .config import settings
from .db import now_iso
from .embeddings import embedding_service
from .index import vector_index
from .parsing import ParseError
from .web_content import fetch


def _prepare(raw, url, fetched_at=None):
    try:
        article = web_content.extract_article(raw, url, public=url not in web_content.CONFIRMED_URLS)
        pieces = web_content.build_pieces(article, TokenizerAdapter(embedding_service.tokenizer_or_none()),
                                          settings.chunk_max_tokens, settings.chunk_overlap_tokens,
                                          settings.parse_max_chunks)
    except (ValueError, ParseError) as exc:
        raise ingest.IngestError(str(exc), 422, 'web_parse_failed') from exc
    source = article['source']
    metadata = {'format':1, 'kind':'web', **source, 'fetched_at':fetched_at or source['fetched_at'],
                'status':article['status'], 'warnings':article['warnings'],
                'image_count':len(article['images']), 'block_count':len(article['blocks'])}
    metadata = web_content.metadata(metadata)
    if metadata is None:
        raise ingest.IngestError('网页来源元数据无效', 422, 'web_metadata_invalid')
    return metadata, pieces, embedding_service.embed_texts([piece.text for piece in pieces])


def _save_chunks(db, doc_id, metadata, pieces, vectors):
    db.execute('DELETE FROM chunks WHERE document_id=?', (doc_id,))
    db.executemany('INSERT INTO chunks (document_id,seq,page,paragraph,token_count,content,vector) '
                   'VALUES (?,?,NULL,?,?,?,?)',
                   [(doc_id, i, piece.paragraph, piece.token_count, piece.text, vectors[i].tobytes())
                    for i, piece in enumerate(pieces)])
    db.execute("UPDATE documents SET status='ready',error=NULL,num_chunks=?,pages=NULL,source_metadata=?,updated_at=? WHERE id=?",
               (len(pieces), json.dumps(metadata, ensure_ascii=False), now_iso(), doc_id))


def import_article(db, url, user, version='1.0', ip=''):
    try:
        url = public_web.normalize_url(url)
    except ValueError as exc:
        raise ingest.IngestError(str(exc), 422, 'web_url_not_allowed') from exc
    with ingest.ingest_lock:
        ingest._ready_or_raise()
        try:
            if url in web_content.CONFIRMED_URLS:
                raw = fetch(url)
            else:
                raw, url = public_web.fetch(url)
        except (URLError, OSError, ValueError, HTTPException) as exc:
            raise ingest.IngestError('网页读取失败，请重试：'+str(exc), 502, 'web_fetch_failed') from exc
        sha = hashlib.sha256(raw).hexdigest()
        duplicate = db.execute('SELECT id,filename FROM documents WHERE sha256=?', (sha,)).fetchone()
        if duplicate:
            raise ingest.IngestError(f"相同网页快照已存在：#{duplicate['id']}《{duplicate['filename']}》", 409, 'duplicate')
        metadata, pieces, vectors = _prepare(raw, url)
        doc = None
        db.execute('BEGIN IMMEDIATE')
        try:
            # 原件按纯文本保存；文件名用于显示，不作为存储路径。
            title = metadata['title'].replace('/', '_').replace('\\', '_')[:200]
            doc, _ = ingest.register_bytes(db, filename=title+'.html.snapshot.txt', content_type='text/plain',
                                           data=raw, user_id=user.id, version=version,
                                           tags=['web_status='+metadata['status']])
            _save_chunks(db, doc['id'], metadata, pieces, vectors)
            audit.log_audit(db, action='web_import', user_id=user.id, username=user.username,
                            detail=f"doc:{doc['id']} url:{url} sha256:{sha} chunks:{len(pieces)}", ip=ip)
            vector_index.reload(db)
            db.commit()
        except BaseException:
            db.rollback()
            if doc:
                (settings.upload_dir/doc['stored_name']).unlink(missing_ok=True)
            vector_index.reload(db)
            raise
        return ingest.doc_dict(ingest._fetch_doc(db, doc['id']))


def reindex_document(db, doc, *, user=None, ip=''):
    """只读取保存快照，不重新访问远端；准备失败时保留原有索引。"""
    metadata = web_content.metadata(doc['source_metadata'])
    if metadata is None or metadata['sha256'] != doc['sha256']:
        raise ingest.IngestError('网页来源元数据损坏，不能按普通文本重建', 409, 'web_metadata_invalid')
    root = settings.upload_dir.resolve()
    path = (root/doc['stored_name']).resolve()
    if path.parent != root or not path.is_file():
        raise ingest.IngestError('网页原始快照不存在', 409, 'web_source_missing')
    with path.open('rb') as source:
        raw = source.read(web_content.MAX_BYTES+1)
    if hashlib.sha256(raw).hexdigest() != doc['sha256']:
        raise ingest.IngestError('网页原始快照SHA不一致，已保留原索引', 409, 'web_source_changed')
    ingest._ready_or_raise()
    metadata, pieces, vectors = _prepare(raw, metadata['url'], metadata['fetched_at'])
    with path.open('rb') as source:
        current_raw = source.read(web_content.MAX_BYTES+1)
    if hashlib.sha256(current_raw).hexdigest() != doc['sha256']:
        raise ingest.IngestError('网页原件在重建期间变化，已保留原索引', 409, 'web_source_changed')
    db.execute('BEGIN IMMEDIATE')
    try:
        current = ingest._fetch_doc(db, doc['id'])
        if current is None or current['sha256'] != doc['sha256'] or current['source_metadata'] != doc['source_metadata']:
            raise ingest.IngestError('网页记录在重建期间变化，请刷新后重试', 409, 'web_source_changed')
        _save_chunks(db, doc['id'], metadata, pieces, vectors)
        if user is not None:
            audit.log_audit(db, action='doc_reindex', user_id=user.id, username=user.username,
                            detail=f"doc:{doc['id']} saved_web_snapshot sha256:{doc['sha256']} chunks:{len(pieces)}", ip=ip)
        vector_index.reload(db)
        db.commit()
    except BaseException:
        db.rollback()
        vector_index.reload(db)
        raise
    return ingest.doc_dict(ingest._fetch_doc(db, doc['id']))
