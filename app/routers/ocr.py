"""Explicit PDF/image OCR preparation/review endpoints for document managers."""
import json
from pathlib import Path
from typing import Annotated
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Path as PathParam, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .. import audit, image_analysis, ingest, ocr_upload
from ..devices import DEVICES, normalize_device
from ..db import get_db, now_iso
from ..deps import require_kb_admin
from .admin import _clean_filename, _clean_version, _raise_embed, _raise_ingest, _require_visibility_permission
from ..embeddings import EmbeddingUnavailable
from ..config import settings
from ..schemas import MAX_SQLITE_ID

router=APIRouter(prefix='/admin')
DocumentId=Annotated[int,PathParam(ge=1,le=MAX_SQLITE_ID)]


class ReviewBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    accepted_suspect_pages: list[StrictInt]=Field(default_factory=list,max_length=50)
    acknowledge_excluded_pages: StrictBool=False
    review_token: StrictStr | None=Field(default=None,pattern=r'^[0-9a-f]{32}\.json$')


class CaptionReviewBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    caption: StrictStr=Field(min_length=1,max_length=2000)
    confirmed: StrictBool
    review_token: StrictStr=Field(pattern=r'^[0-9a-f]{32}\.json$')


@router.get('/ocr/status')
def ocr_status(user=Depends(require_kb_admin)):
    return ocr_upload.availability()


@router.post('/image-analysis')
def image_analysis_draft(file:UploadFile=File(...), caption:str=Form(''), user=Depends(require_kb_admin)):
    if len(caption) > 2000:
        raise HTTPException(422, '图片说明最多2000字')
    raw = file.file.read(image_analysis.MAX_IMAGE_BYTES + 1)
    try:
        return image_analysis.analyze(_clean_filename(file.filename), raw, caption)
    except image_analysis.AnalysisError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.post('/documents/image-text',status_code=201)
def upload_image_text(request:Request, file:UploadFile=File(...), version:str=Form('1.0'),
                      visibility:str=Form('shared'), device:str=Form(''), caption:str=Form(''),
                      db=Depends(get_db), user=Depends(require_kb_admin)):
    filename=_clean_filename(file.filename)
    try:
        device=normalize_device(device)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    caption=(caption or '').strip()
    if not caption or len(caption)>2000:
        raise HTTPException(422,'请填写最多2000字的图片说明')
    _require_visibility_permission(user,visibility)
    data=file.file.read(image_analysis.MAX_IMAGE_BYTES+1)
    try:
        image_analysis.validate_image(filename,data)
        return ocr_upload.create_image_text(db,filename,file.content_type or '',data,caption,device,
                                             _clean_version(version),visibility,user,
                                             request.client.host if request.client else '')
    except image_analysis.AnalysisError as exc:
        raise HTTPException(exc.status,exc.message) from exc
    except ingest.IngestError as exc:
        _raise_ingest(exc)
    except EmbeddingUnavailable as exc:
        _raise_embed(exc)


@router.post('/documents/ocr',status_code=202)
def upload_pdf(background:BackgroundTasks, request:Request, file:UploadFile=File(...), version:str=Form('1.0'),
               visibility:str=Form('shared'), device:str=Form(''), caption:str=Form(''), db=Depends(get_db), user=Depends(require_kb_admin)):
    filename=_clean_filename(file.filename)
    try:
        device=normalize_device(device)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    caption=(caption or '').strip()
    if len(caption)>2000:
        raise HTTPException(422,'图说明最多2000字')
    image=Path(filename).suffix.lower() in {'.png','.jpg','.jpeg'}
    if not image and not filename.lower().endswith('.pdf'):
        raise HTTPException(422,'OCR上传支持PDF、PNG和JPEG')
    _require_visibility_permission(user,visibility)
    state=ocr_upload.availability()
    if not state['available']:
        raise HTTPException(503,state['message'])
    if db.execute("SELECT COUNT(*) FROM ocr_jobs WHERE state IN ('queued','running')").fetchone()[0]>=10:
        raise HTTPException(409,'OCR待处理任务已达到10份，请稍后再上传')
    limit=(10 if image else 20)*1024*1024
    data=file.file.read(limit+1)
    if len(data)>limit:
        raise HTTPException(413,'OCR上传：图片限10MB，PDF限20MB/50页')
    doc=None
    db.execute('BEGIN IMMEDIATE')
    try:
        doc,_=ingest.register_bytes(db,filename=filename,content_type=file.content_type or 'application/pdf',data=data,
                                    user_id=user.id,version=_clean_version(version),visibility=visibility,ocr_image=image)
        now=now_iso()
        db.execute("UPDATE documents SET device=?,caption=? WHERE id=?",(device,caption,doc['id']))
        db.execute("INSERT INTO ocr_jobs (document_id,state,created_at,updated_at) VALUES (?,'queued',?,?)",(doc['id'],now,now))
        audit.log_audit(db,action='ocr_upload',user_id=user.id,username=user.username,detail=f"doc:{doc['id']} waiting_for_review",ip=request.client.host if request.client else '')
        db.commit()
    except BaseException as exc:
        db.rollback()
        if doc is not None:
            (settings.upload_dir/doc['stored_name']).unlink(missing_ok=True)
        if isinstance(exc,ingest.IngestError):
            _raise_ingest(exc)
        if isinstance(exc,EmbeddingUnavailable):
            _raise_embed(exc)
        raise
    background.add_task(ocr_upload.run_job,doc['id'])
    return {'id':doc['id'],'state':'queued','filename':filename}


@router.get('/documents/{document_id}/ocr')
def inspect_ocr(document_id:DocumentId,db=Depends(get_db),user=Depends(require_kb_admin)):
    job=db.execute('SELECT * FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone()
    doc=db.execute('SELECT filename,stored_name,sha256,status,tags,caption FROM documents WHERE id=?',(document_id,)).fetchone()
    if job is None or doc is None:
        raise HTTPException(404,'OCR任务不存在')
    image=Path(doc['stored_name']).suffix.lower() in {'.png','.jpg','.jpeg'}
    result={'document_id':document_id,'filename':doc['filename'],'source_kind':'image' if image else 'pdf',
            'state':job['state'],'error':job['error'],'pages':[]}
    try:
        tags=json.loads(doc['tags'] or '[]')
    except (TypeError,ValueError):
        tags=[]
    result['indexed_unreviewed']=bool(image and doc['status']=='ready' and isinstance(tags,list)
                                      and 'ocr_human_review_pending' in tags)
    result['caption_only']=bool(image and doc['status']=='ready' and isinstance(tags,list) and 'image_caption_only' in tags)
    result['caption']=doc['caption'] or ''
    if job['state'] in ('review','ready'):
        try:
            payload=ocr_upload.read_result(job)
            pages=ocr_upload.validate(payload,doc['sha256'],image=image)
            result['pages']=[{key:p[key] for key in ('page','status','source','text','warnings','confidence','uncertain_lines',
                'lines','original_image_size','ocr_image_size','geometry_coordinate_system','rotation_degrees') if key in p} for p in pages]
            result['accepted_pages']=json.loads(job['accepted_pages'])
            result['review_token']=job['result_name']
        except ingest.IngestError as exc:
            _raise_ingest(exc)
    return result


@router.post('/documents/{document_id}/ocr/retry',status_code=202)
def retry_ocr(document_id:DocumentId,background:BackgroundTasks,request:Request,db=Depends(get_db),user=Depends(require_kb_admin)):
    state=ocr_upload.availability()
    if not state['available']:
        raise HTTPException(503,state['message'])
    try:
        ocr_upload.retry(db,document_id,user=user,ip=request.client.host if request.client else '')
    except ingest.IngestError as exc:
        _raise_ingest(exc)
    background.add_task(ocr_upload.run_job,document_id)
    return {'document_id':document_id,'state':'queued'}


@router.post('/documents/{document_id}/ocr/publish')
def publish_ocr(document_id:DocumentId,body:ReviewBody,request:Request,db=Depends(get_db),user=Depends(require_kb_admin)):
    try:
        return ocr_upload.publish(db,document_id,body.accepted_suspect_pages,body.acknowledge_excluded_pages,user,request.client.host if request.client else '',review_token=body.review_token)
    except ingest.IngestError as exc:
        _raise_ingest(exc)
    except EmbeddingUnavailable as exc:
        _raise_embed(exc)


@router.post('/documents/{document_id}/ocr/publish-caption')
def publish_image_caption(document_id:DocumentId,body:CaptionReviewBody,request:Request,db=Depends(get_db),user=Depends(require_kb_admin)):
    try:
        return ocr_upload.publish_caption(db,document_id,body.caption.strip(),body.confirmed,user,
                                          request.client.host if request.client else '',review_token=body.review_token)
    except ingest.IngestError as exc:
        _raise_ingest(exc)
    except EmbeddingUnavailable as exc:
        _raise_embed(exc)
