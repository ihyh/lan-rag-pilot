"""Persisted OCR preparation, explicit human review and transactional indexing."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

from . import audit, ingest, parsing
from .chunking import TokenizerAdapter, chunk_units
from .config import BASE_DIR, settings
from .db import atomic, connect, now_iso
from .embeddings import embedding_service
from .index import vector_index

_process_lock = threading.Lock()


def runtime():
    configured = os.environ.get('RAG_OCR_PYTHON')
    optional = BASE_DIR / '.venv-ocr' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return Path(configured) if configured else optional if optional.is_file() else Path(sys.executable)


def availability():
    try:
        process = subprocess.run([str(runtime()), '-I', str(BASE_DIR/'scripts/ocr_upload_worker.py'), '--probe'],
                                 capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result = json.loads(process.stdout)
        return {'available':process.returncode == 0 and result.get('available') is True,
                'message':'OCR 已就绪' if result.get('available') else 'OCR 独立依赖尚未就绪，请先安装 requirements-ocr.txt 到 .venv-ocr'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'available':False,'message':'OCR 独立环境不可用，请检查 .venv-ocr 或 RAG_OCR_PYTHON'}


def artifact(document_id, name):
    if not re.fullmatch(r'[0-9a-f]{32}\.json', name or ''):
        raise ingest.IngestError('OCR 结果标识无效', code='ocr_result_invalid')
    return settings.data_dir/'ocr-results'/str(document_id)/name


def read_result(job):
    path = artifact(job['document_id'], job['result_name'])
    if not path.is_file() or path.stat().st_size > 40 * 1024 * 1024:
        raise ingest.IngestError('OCR 结果不存在或过大，请重新处理', code='ocr_result_invalid')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ingest.IngestError('OCR结果损坏，请重新处理', code='ocr_result_invalid') from exc


def validate(result, expected_sha, *, allow_skipped=False, image=False):
    if not isinstance(result,dict) or not isinstance(result.get('source'),dict):
        raise ingest.IngestError('OCR结果格式无效', code='ocr_result_invalid')
    if result['source'].get('sha256') != expected_sha:
        raise ingest.IngestError('OCR 来源与原件SHA不一致，拒绝入库', code='ocr_source_mismatch')
    count = result['source'].get('pages')
    pages = result.get('pages')
    if image and (result['source'].get('kind')!='image' or count!=1):
        raise ingest.IngestError('图片OCR来源类型或帧数错误',code='ocr_source_mismatch')
    if type(count) is not int or not 1 <= count <= 50 or not isinstance(pages,list) or len(pages)!=count:
        raise ingest.IngestError('OCR 页数不完整或超限', code='ocr_pages_invalid')
    ids = [p.get('page') for p in pages if isinstance(p,dict)]
    if any(type(p) is not int for p in ids) or set(ids) != set(range(1,count+1)) or len(ids)!=count:
        raise ingest.IngestError('OCR 页码重复、遗漏或越界', code='ocr_pages_invalid')
    allowed = {'ok','suspect','failed'} | ({'skipped'} if allow_skipped else set())
    if any(p.get('status') not in allowed or not isinstance(p.get('text',''),str) for p in pages):
        raise ingest.IngestError('OCR 页面状态或正文无效', code='ocr_pages_invalid')
    if any(p['status'] in ('ok','suspect') and not p.get('text','').strip() for p in pages):
        raise ingest.IngestError('OCR可用页没有正文，请重新处理', code='ocr_pages_invalid')
    if sum(len(p.get('text','')) for p in pages)>4_000_000:
        raise ingest.IngestError('OCR 正文超过400万字符限制', code='ocr_text_limit')
    return sorted(pages,key=lambda p:p['page'])


def source_path(doc):
    root = settings.upload_dir.resolve()
    path = (root/doc['stored_name']).resolve()
    if path.parent != root or not path.is_file():
        raise ingest.IngestError('OCR原件不存在', code='ocr_source_missing')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=doc['sha256']:
        raise ingest.IngestError('OCR原件内容已改变，拒绝处理', code='ocr_source_mismatch')
    return path


def process_pdf(path, output, pages):
    command=[str(runtime()),'-I',str(BASE_DIR/'scripts/ocr_upload_worker.py'),'--source',str(path),'--output',str(output)]
    if pages:
        command += ['--pages',','.join(map(str,pages))]
    with output.with_suffix('.log').open('x',encoding='utf-8') as log:
        try:
            process=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=180,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired as exc:
            raise ingest.IngestError('OCR 超过180秒，子进程已停止；可以重试',code='ocr_timeout') from exc
    if process.returncode != 0:
        raise ingest.IngestError('OCR处理失败：PDF限20MB/50页，图片限10MB/2000万像素单帧PNG/JPEG，请核对原件后重试',code='ocr_failed')
    if not output.is_file() or output.stat().st_size > 40 * 1024 * 1024:
        raise ingest.IngestError('OCR结果不存在或过大',code='ocr_result_invalid')
    return json.loads(output.read_text(encoding='utf-8'))


def run_job(document_id):
    # ponytail: one CPU OCR process at a time; a general durable import worker is a later feature.
    with _process_lock, closing(connect()) as db:
        job=db.execute('SELECT * FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
        doc=db.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        if job is None or doc is None or job['state']!='queued':
            return
        db.execute("UPDATE ocr_jobs SET state='running',error=NULL,updated_at=? WHERE document_id=?",(now_iso(),document_id))
        try:
            path=source_path(doc)
            image=path.suffix.lower() in {'.png','.jpg','.jpeg'}
            previous=read_result(job) if job['result_name'] else None
            failed=[p['page'] for p in validate(previous,doc['sha256'],image=image) if p['status']=='failed'] if previous else []
            folder=settings.data_dir/'ocr-results'/str(document_id)
            folder.mkdir(parents=True,exist_ok=True)
            raw=folder/(uuid.uuid4().hex+'.json')
            result=process_pdf(path,raw,failed)
            pages=validate(result,doc['sha256'],allow_skipped=bool(failed),image=image)
            if failed:
                old={p['page']:p for p in previous['pages']}
                for page in pages:
                    if page['page'] in failed:
                        if page['status']=='skipped':
                            raise ingest.IngestError('失败页重试未处理所选页面',code='ocr_pages_invalid')
                        old[page['page']]=page
                result['pages']=[old[p] for p in sorted(old)]
                validate(result,doc['sha256'],image=image)
            # Per-page records are authoritative after merging a failed-page retry.
            result.pop('summary',None)
            source_path(doc)
            name=uuid.uuid4().hex+'.json'
            with artifact(document_id,name).open('x',encoding='utf-8') as stream:
                json.dump(result,stream,ensure_ascii=False)
            db.execute("UPDATE ocr_jobs SET state='review',result_name=?,error=NULL,updated_at=? WHERE document_id=? AND state='running'",
                       (name,now_iso(),document_id))
        except Exception as exc:
            message=exc.message if isinstance(exc,ingest.IngestError) else 'OCR处理发生错误，请重试并核对原件'
            db.execute("UPDATE ocr_jobs SET state='failed',error=?,updated_at=? WHERE document_id=?",(message,now_iso(),document_id))
            db.execute("UPDATE documents SET status='failed',error=? WHERE id=? AND status='parsing'",(message,document_id))


def retry(db, document_id, *, user=None, ip=''):
    job=db.execute('SELECT * FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
    if job is None:
        raise ingest.IngestError('OCR任务不存在',404,'ocr_not_found')
    doc=db.execute('SELECT tags FROM documents WHERE id=?',(document_id,)).fetchone()
    try:
        tags=json.loads(doc['tags'] or '[]') if doc else []
    except (TypeError,ValueError):
        tags=[]
    if isinstance(tags,list) and 'image_caption_only' in tags:
        raise ingest.IngestError('图片已按人工说明入库，请使用编辑图片说明',409,'caption_only_no_retry')
    if job['state'] in ('queued','running'):
        raise ingest.IngestError('OCR任务正在处理中',409,'ocr_busy')
    if job['state']=='ready' and not any(p['status']=='failed' for p in read_result(job)['pages']):
        raise ingest.IngestError('当前没有失败页可重试',409,'ocr_no_failed_pages')
    with atomic(db):
        changed=db.execute("UPDATE ocr_jobs SET state='queued',error=NULL,updated_at=? WHERE document_id=? AND state=?",
                           (now_iso(),document_id,job['state'])).rowcount
        if not changed:
            raise ingest.IngestError('OCR任务状态已变化，请刷新',409,'ocr_busy')
        db.execute("UPDATE documents SET status='parsing',error=NULL WHERE id=? AND status='failed'",(document_id,))
        if user is not None:
            audit.log_audit(db,action='ocr_retry',user_id=user.id,username=user.username,
                            detail=f'doc:{document_id}',ip=ip)


def _caption_pieces(caption, tokenizer):
    caption=re.sub(r'^(?:图说明|说明)\s*[：:]\s*','',caption.strip())
    if not caption:
        return []
    return chunk_units([parsing.Unit('图说明：'+caption,page=None,paragraph=None)],tokenizer,
                       settings.chunk_max_tokens,settings.chunk_overlap_tokens,max_pieces=4)


def create_image_text(db, filename, content_type, data, caption, device, version, visibility, user, ip):
    """Save a validated image and its human description without creating an OCR job."""
    with ingest.ingest_lock:
        ingest._ready_or_raise()
        tokenizer=TokenizerAdapter(embedding_service.tokenizer_or_none())
        try:
            pieces=_caption_pieces(caption,tokenizer)
        except parsing.ParseError as exc:
            raise ingest.IngestError(exc.message,422,exc.code) from exc
        if not pieces:
            raise ingest.IngestError('请填写非空图片说明',422,'caption_required')
        vectors=embedding_service.embed_texts([p.text for p in pieces])
        doc=None
        db.execute('BEGIN IMMEDIATE')
        try:
            doc,_=ingest.register_bytes(db,filename=filename,content_type=content_type,data=data,
                user_id=user.id,version=version,visibility=visibility,ocr_image=True,
                tags=['image_caption_only','image_text_direct','ocr_visual_relations_unverified'])
            source_path(doc)
            db.executemany('INSERT INTO chunks (document_id,seq,page,paragraph,token_count,content,vector) VALUES (?,?,?,?,?,?,?)',
                [(doc['id'],i,p.page,p.paragraph,p.token_count,p.text,vectors[i].tobytes()) for i,p in enumerate(pieces)])
            db.execute("UPDATE documents SET device=?,caption=?,status='ready',num_chunks=?,updated_at=? WHERE id=?",
                (device,caption,len(pieces),now_iso(),doc['id']))
            audit.log_audit(db,action='image_text_upload',user_id=user.id,username=user.username,
                            detail=f"doc:{doc['id']} human_caption_only",ip=ip)
            with vector_index._lock:
                try:
                    vector_index.reload(db)
                    db.commit()
                except BaseException:
                    db.rollback()
                    vector_index.reload(db)
                    raise
        except BaseException:
            db.rollback()
            if doc is not None:
                (settings.upload_dir/doc['stored_name']).unlink(missing_ok=True)
            raise
        return {'id':doc['id'],'filename':filename,'state':'ready'}


def update_caption(db, document_id, caption, expected_caption, user, ip):
    """Replace only verified caption chunks; retain OCR chunks and historical excerpts."""
    with ingest.ingest_lock:
        doc=db.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        if doc is None:
            raise ingest.IngestError('文档不存在',404,'not_found')
        job=db.execute('SELECT * FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
        try:
            tags=json.loads(doc['tags'])
        except (TypeError,ValueError):
            tags=[]
        caption_only=isinstance(tags,list) and 'image_caption_only' in tags
        direct_image=caption_only and 'image_text_direct' in tags and job is None
        if (Path(doc['stored_name']).suffix.lower() not in {'.png','.jpg','.jpeg'}
                or doc['status']!='ready' or (not direct_image and (job is None or job['state']!='ready'))
                or not isinstance(tags,list) or not (caption_only or 'ocr_image_text_only' in tags)):
            raise ingest.IngestError('仅支持已完成 OCR 入库的图片修改说明',409,'caption_image_not_ready')
        if doc['caption']!=expected_caption:
            raise ingest.IngestError('图片说明已被修改，请刷新后重试',409,'caption_changed')
        source_path(doc)
        ingest._ready_or_raise()
        tokenizer=TokenizerAdapter(embedding_service.tokenizer_or_none())
        try:
            old_pieces=_caption_pieces(doc['caption'],tokenizer)
            new_pieces=_caption_pieces(caption,tokenizer)
        except parsing.ParseError as exc:
            raise ingest.IngestError(exc.message,422,exc.code) from exc
        if caption_only and not new_pieces:
            raise ingest.IngestError('此图片仅靠说明检索，请保留非空图片说明',422,'caption_required')
        rows=db.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY seq',(document_id,)).fetchall()
        n=len(old_pieces)
        note='图片OCR文字（仅文字已核对，图表关系和阅读顺序未验证）：\n'
        if ((len(rows)!=n or n==0) if caption_only else len(rows)<=n) or (doc['num_chunks']!=len(rows)
                or any(row['seq']!=i for i,row in enumerate(rows))
                or any(row['page'] is not None or row['paragraph'] is not None or row['content']!=piece.text
                       for row,piece in zip(rows[:n],old_pieces))
                or any(row['page'] is not None or row['paragraph']!=1 or not row['content'].startswith(note)
                       for row in rows[n:])):
            raise ingest.IngestError('现有图片说明切片无法安全识别，可能是切片设置已变化；请保留资料并联系维护人员',409,'caption_layout_unrecognized')
        if caption==doc['caption']:
            return {'id':document_id,'caption':caption}
        vectors=embedding_service.embed_texts([p.text for p in new_pieces]) if new_pieces else []
        if new_pieces and vector_index.size()>0 and embedding_service.dim!=vector_index.dim():
            raise ingest.IngestError('向量维度不一致，不能更新图片说明',409,'dim_mismatch')
        db.execute('BEGIN IMMEDIATE')
        try:
            current=db.execute('SELECT caption,status,stored_name,sha256,tags,num_chunks FROM documents WHERE id=?',(document_id,)).fetchone()
            current_job=db.execute('SELECT state,result_name,accepted_pages FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
            current_rows=db.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY seq',(document_id,)).fetchall()
            if (current is None or tuple(current)!=(doc['caption'],doc['status'],doc['stored_name'],doc['sha256'],doc['tags'],doc['num_chunks'])
                    or (current_job is not None if direct_image else current_job is None or tuple(current_job)!=(job['state'],job['result_name'],job['accepted_pages']))
                    or [tuple(row) for row in current_rows]!=[tuple(row) for row in rows]):
                raise ingest.IngestError('资料已变化，请刷新后重试',409,'caption_changed')
            source_path(doc)
            for row in rows[:n]:
                db.execute('DELETE FROM chunks WHERE id=?',(row['id'],))
            for row in rows[n:]:
                db.execute('UPDATE chunks SET seq=? WHERE id=?',(-row['seq']-1,row['id']))
            for i,p in enumerate(new_pieces):
                db.execute('INSERT INTO chunks (document_id,seq,page,paragraph,token_count,content,vector) VALUES (?,?,?,?,?,?,?)',
                           (document_id,i,p.page,p.paragraph,p.token_count,p.text,vectors[i].tobytes()))
            for i,row in enumerate(rows[n:],len(new_pieces)):
                db.execute('UPDATE chunks SET seq=? WHERE id=?',(i,row['id']))
            db.execute('UPDATE documents SET caption=?,num_chunks=?,updated_at=? WHERE id=?',
                       (caption,len(new_pieces)+len(rows)-n,now_iso(),document_id))
            audit.log_audit(db,action='image_caption_update',user_id=user.id,username=user.username,
                            detail=f'doc:{document_id}',ip=ip)
            # Search must not observe the new in-memory index before the DB commit.
            with vector_index._lock:
                try:
                    vector_index.reload(db)
                    db.commit()
                except BaseException:
                    db.rollback()
                    vector_index.reload(db)
                    raise
        except BaseException:
            db.rollback()
            vector_index.reload(db)
            raise
        return {'id':document_id,'caption':caption}


def publish_caption(db, document_id, caption, confirmed, user, ip, *, review_token):
    """Index a human description when an inspected image has no usable OCR text."""
    if not confirmed:
        raise ingest.IngestError('请确认图片说明是对照原图填写的',422,'caption_confirmation_required')
    with ingest.ingest_lock:
        job=db.execute('SELECT * FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
        doc=db.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        if job is None or doc is None:
            raise ingest.IngestError('OCR任务不存在',404,'ocr_not_found')
        if job['state']!='review' or review_token!=job['result_name']:
            raise ingest.IngestError('识别结果已变化，请重新打开核对窗口',409,'ocr_state_changed')
        if source_path(doc).suffix.lower() not in {'.png','.jpg','.jpeg'}:
            raise ingest.IngestError('仅图片可使用人工说明入库',422,'caption_image_required')
        pages=validate(read_result(job),doc['sha256'],image=True)
        if any(p['status'] in ('ok','suspect') for p in pages):
            raise ingest.IngestError('已有可核对的OCR文字，请使用文字核对入库',409,'ocr_text_available')
        if doc['status']=='ready' or doc['num_chunks']!=0 or db.execute('SELECT 1 FROM chunks WHERE document_id=?',(document_id,)).fetchone():
            raise ingest.IngestError('资料已有索引，不能覆盖入库',409,'caption_existing_index')
        ingest._ready_or_raise()
        tokenizer=TokenizerAdapter(embedding_service.tokenizer_or_none())
        try:
            pieces=_caption_pieces(caption,tokenizer)
        except parsing.ParseError as exc:
            raise ingest.IngestError(exc.message,422,exc.code) from exc
        if not pieces:
            raise ingest.IngestError('请填写非空图片说明',422,'caption_required')
        vectors=embedding_service.embed_texts([p.text for p in pieces])
        if vector_index.size()>0 and embedding_service.dim!=vector_index.dim():
            raise ingest.IngestError('向量维度不一致，不能入库图片说明',409,'dim_mismatch')
        try:
            tags=json.loads(doc['tags'] or '[]')
        except (TypeError,ValueError) as exc:
            raise ingest.IngestError('资料标签无法识别，请联系维护人员',409,'caption_tags_invalid') from exc
        if not isinstance(tags,list):
            raise ingest.IngestError('资料标签无法识别，请联系维护人员',409,'caption_tags_invalid')
        tags=[tag for tag in tags if not (isinstance(tag,str) and tag.startswith('ocr_'))]
        tags.extend(['image_caption_only','ocr_visual_relations_unverified'])
        db.execute('BEGIN IMMEDIATE')
        try:
            current=db.execute('SELECT status,caption,stored_name,sha256,tags,num_chunks FROM documents WHERE id=?',(document_id,)).fetchone()
            current_job=db.execute('SELECT state,result_name FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
            if (current is None or tuple(current)!=(doc['status'],doc['caption'],doc['stored_name'],doc['sha256'],doc['tags'],doc['num_chunks'])
                    or current_job is None or tuple(current_job)!=(job['state'],job['result_name'])
                    or db.execute('SELECT 1 FROM chunks WHERE document_id=?',(document_id,)).fetchone()):
                raise ingest.IngestError('资料或识别结果已变化，请刷新后重试',409,'caption_changed')
            source_path(doc)
            db.executemany('INSERT INTO chunks (document_id,seq,page,paragraph,token_count,content,vector) VALUES (?,?,?,?,?,?,?)',
                           [(document_id,i,p.page,p.paragraph,p.token_count,p.text,vectors[i].tobytes()) for i,p in enumerate(pieces)])
            db.execute("UPDATE documents SET caption=?,status='ready',error=NULL,num_chunks=?,pages=NULL,tags=?,updated_at=? WHERE id=?",
                       (caption,len(pieces),json.dumps(tags,ensure_ascii=False),now_iso(),document_id))
            db.execute("UPDATE ocr_jobs SET state='ready',accepted_pages='[]',updated_at=? WHERE document_id=?",(now_iso(),document_id))
            audit.log_audit(db,action='image_caption_publish',user_id=user.id,username=user.username,
                            detail=f'doc:{document_id} human_caption_only',ip=ip)
            with vector_index._lock:
                try:
                    vector_index.reload(db)
                    db.commit()
                except BaseException:
                    db.rollback()
                    vector_index.reload(db)
                    raise
        except BaseException:
            db.rollback()
            raise
        return {'document_id':document_id,'state':'ready','content_scope':'image_caption_only'}


def publish(db, document_id, accepted, acknowledge, user, ip, *, review_token=None):
    with ingest.ingest_lock:
        job=db.execute('SELECT * FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
        doc=db.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone()
        if job is None or doc is None:
            raise ingest.IngestError('OCR任务不存在',404,'ocr_not_found')
        if job['state']!='review':
            raise ingest.IngestError('只有待核对结果可以确认入库',409,'ocr_state_invalid')
        path=source_path(doc)
        image=path.suffix.lower() in {'.png','.jpg','.jpeg'}
        if (image or review_token is not None) and review_token!=job['result_name']:
            raise ingest.IngestError('识别结果已变化或尚未核对，请重新打开核对窗口',409,'ocr_state_changed')
        result=read_result(job)
        pages=validate(result,doc['sha256'],image=image)
        suspect={p['page'] for p in pages if p['status']=='suspect'}
        reviewable={p['page'] for p in pages if p['status'] in ('ok','suspect')} if image else suspect
        if not set(accepted)<=reviewable:
            raise ingest.IngestError('只能逐项确认可疑页；失败页不能入库',422,'ocr_review_invalid')
        if image and not accepted:
            raise ingest.IngestError('请对照原图确认OCR文字后再入库；图表关系未验证',422,'ocr_image_review_required')
        selected=[p for p in pages if p['page'] in accepted or (not image and p['status']=='ok')]
        if len(selected)!=len(pages) and not acknowledge:
            raise ingest.IngestError('存在不入库的页面，请确认排除页后再保存',422,'ocr_exclusion_unconfirmed')
        units=[parsing.Unit(p['text'].strip(),page=None if image else p['page'],paragraph=1 if image else None)
               for p in selected if p.get('text','').strip()]
        if not units:
            raise ingest.IngestError('没有可入库的正文，请先重试或核对原件',422,'ocr_empty')
        ingest._ready_or_raise()
        tokenizer=TokenizerAdapter(embedding_service.tokenizer_or_none())
        note='图片OCR文字（仅文字已核对，图表关系和阅读顺序未验证）：\n' if image else ''
        budget=settings.chunk_max_tokens-tokenizer.count(note) if note else settings.chunk_max_tokens
        if budget<=0:
            raise ingest.IngestError('切片预算不足以保留图片OCR范围说明，请调整切片大小',422,'ocr_chunk_budget')
        pieces=chunk_units(units,tokenizer,budget,min(settings.chunk_overlap_tokens,budget-1) if image else settings.chunk_overlap_tokens,
                           max_pieces=settings.parse_max_chunks)
        if image:
            for piece in pieces:
                piece.text=note+piece.text
                piece.token_count=tokenizer.count(piece.text)
            caption=(doc['caption'] or '').strip() if 'caption' in doc.keys() else ''
            pieces=list(_caption_pieces(caption,tokenizer))+list(pieces)
        vectors=embedding_service.embed_texts([p.text for p in pieces])
        source_path(doc)
        tags=json.loads(doc['tags'])
        tags=[tag for tag in tags if not (isinstance(tag,str) and tag.startswith('ocr_'))]
        tags.append('ocr') if 'ocr' not in tags else None
        for status in ('ok','suspect','failed'):
            numbers=[str(p['page']) for p in pages if p['status']==status]
            if numbers:
                tags.append('ocr_'+status+('_images=' if image else '_pages=')+','.join(numbers))
        if image:
            tags.extend(['ocr_image_text_only','ocr_visual_relations_unverified'])
        excluded=sorted(suspect-set(accepted))
        if excluded:
            tags.append('ocr_excluded_suspect_pages='+','.join(map(str,excluded)))
        db.execute('BEGIN IMMEDIATE')
        try:
            current=db.execute('SELECT state,result_name FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
            if current is None or current['state']!='review' or current['result_name']!=job['result_name']:
                raise ingest.IngestError('OCR结果已变化，请重新打开核对',409,'ocr_state_changed')
            db.execute('DELETE FROM chunks WHERE document_id=?',(document_id,))
            db.executemany('INSERT INTO chunks (document_id,seq,page,paragraph,token_count,content,vector) VALUES (?,?,?,?,?,?,?)',
                           [(document_id,i,p.page,p.paragraph,p.token_count,p.text,vectors[i].tobytes()) for i,p in enumerate(pieces)])
            db.execute("UPDATE documents SET status='ready',error=NULL,num_chunks=?,pages=?,tags=?,updated_at=? WHERE id=?",
                       (len(pieces),None if image else len(pages),json.dumps(tags,ensure_ascii=False),now_iso(),document_id))
            db.execute("UPDATE ocr_jobs SET state='ready',accepted_pages=?,updated_at=? WHERE document_id=?",
                       (json.dumps(sorted(accepted)),now_iso(),document_id))
            audit.log_audit(db,action='ocr_publish',user_id=user.id,username=user.username,
                            detail=f'doc:{document_id} '+(f'indexed_images:{[p["page"] for p in selected]}' if image else f'indexed_pages:{[u.page for u in units]}')+
                                   f' excluded_failed_or_suspect:{len(pages)-len(selected)}',ip=ip)
            vector_index.reload(db)
            db.commit()
        except BaseException:
            db.rollback()
            vector_index.reload(db)
            raise
        if image:
            return {'document_id':document_id,'indexed_images':[p['page'] for p in selected],
                    'content_scope':'image_text_only','state':'ready'}
        return {'document_id':document_id,'indexed_pages':[u.page for u in units],'excluded_suspect_pages':excluded,'state':'ready'}
