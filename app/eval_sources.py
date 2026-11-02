"""Indexed-source consistency and review-time corpus binding, not answer grading."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from . import acl,log_content
from .config import settings
from .deps import CurrentUser,logical_role


def text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _has_location(source):
    if source.get('location_kind') == 'image_caption':
        return (type(source.get('chunk_id')) is int and source['chunk_id'] > 0
                and all(source.get(key) is None for key in ('page','paragraph','line_start','line_end')))
    if 'line_start' in source or 'line_end' in source:
        start,end=source.get('line_start'),source.get('line_end')
        return (type(start) is int and type(end) is int and 0<start<=end
                and source.get('page') is None and source.get('paragraph') is None)
    return any(type(source.get(key)) is int and source[key]>0 for key in ('page','paragraph'))


def _direct_image(document):
    try:
        tags=json.loads(document['tags'] or '[]')
    except (TypeError,ValueError):
        return False
    return (isinstance(tags,list) and 'image_caption_only' in tags and 'image_text_direct' in tags
            and Path(document['filename']).suffix.lower() in ('.png','.jpg','.jpeg')
            and Path(document['stored_name']).suffix.lower() in ('.png','.jpg','.jpeg')
            and document['source_metadata'] is None)


def _verify_image_file(document):
    root=Path(settings.upload_dir).resolve()
    name=document['stored_name']
    path=(root/name).resolve()
    if Path(name).name != name or path.parent != root:
        raise HTTPException(409,'图片原件路径无效，不能用于复测')
    try:
        if not 0 < path.stat().st_size <= 10*1024*1024:
            raise HTTPException(409,'图片原件大小异常，不能用于复测')
        with path.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError as exc:
        raise HTTPException(409,'图片原件不可读取，不能用于复测') from exc
    if digest != document['sha256']:
        raise HTTPException(409,'图片原件与记录的 SHA 不一致，不能用于复测')


def _log_metadata(document):
    metadata=log_content.metadata(document['source_metadata'])
    if ((metadata is None and log_content.is_log_document(document))
            or metadata is not None and metadata['sha256']!=document['sha256']):
        raise HTTPException(409,'日志来源元数据缺失、损坏或与原件身份不一致，请重新核对')
    return metadata


def _log_location(chunk,metadata):
    start,end=chunk['line_start'],chunk['line_end']
    if metadata is None:
        if start is not None or end is not None:
            raise HTTPException(409,'来源带有日志行号但缺少有效日志元数据，请重新核对')
        return {}
    if (not _has_location(chunk) or end>metadata['line_count']):
        raise HTTPException(409,'日志来源行号缺失、超出范围或与页段坐标混用，请重新核对')
    try:
        _,lines=log_content.parse(chunk['content'].encode('utf-8'),'utf-8')
    except (log_content.LogError,UnicodeError) as exc:
        raise HTTPException(409,'日志片段正文无法核对物理行，请重新核对') from exc
    if len(lines)!=end-start+1:
        raise HTTPException(409,'日志片段正文与物理行范围不一致，请重新核对')
    return {'line_start':start,'line_end':end}


def document_manifest(db,ids):
    items=[]
    for document_id in sorted(ids):
        doc=db.execute('SELECT id,filename,sha256,version,status,lifecycle,applicability,stored_name,tags,source_metadata FROM documents WHERE id=?',(document_id,)).fetchone()
        if doc is None or doc['status']!='ready' or doc['lifecycle']!='active':
            raise HTTPException(409,f'资料#{document_id}已删除、未就绪或已成为历史版本')
        metadata=_log_metadata(doc)
        rows=[dict(row) for row in db.execute('SELECT id,seq,page,paragraph,content,line_start,line_end FROM chunks WHERE document_id=? ORDER BY seq,id',(document_id,))]
        locations=[_log_location(row,metadata) for row in rows]
        chunks=[tuple(row[key] for key in ('id','seq','page','paragraph','content')) for row in rows]
        payload={'chunks':chunks,'applicability':doc['applicability']}
        if _direct_image(doc):
            if db.execute('SELECT 1 FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone():
                raise HTTPException(409,'直接图文资料存在 OCR 任务，不能用于复测')
            _verify_image_file(doc)
            payload['image_caption_tags']=doc['tags']
        if metadata is not None:
            payload.update(log_metadata=doc['source_metadata'],log_lines=locations)
        digest=text_sha(json.dumps(payload,ensure_ascii=False,separators=(',',':')))
        items.append({'document_id':doc['id'],'filename':doc['filename'],'sha256':doc['sha256'],
                      'version':doc['version'],'index_sha256':digest})
    return items


def _owner(db,original):
    row=db.execute('SELECT id,username,role,is_kb_admin,is_active FROM users WHERE id=?',(original['user_id'],)).fetchone()
    if row is None or not row['is_active']:
        raise HTTPException(409,'原提问账号已删除或停用，须重新核对复测范围')
    return CurrentUser(row['id'],row['username'],logical_role(row['role'],row['is_kb_admin']),True)


def _scope_ids(db,original):
    raw=original.get('document_ids')
    if raw is None:
        raise HTTPException(409,'原文档范围缺失，不允许默认为全库')
    allowed=acl.visible_document_ids(db,_owner(db,original))
    ids=set(raw) if raw else {row[0] for row in db.execute("SELECT id FROM documents WHERE status='ready' AND lifecycle='active'")}
    if raw and allowed is not None and not ids<=allowed:
        raise HTTPException(409,'原文档范围含已撤权资料')
    if allowed is not None:ids &= allowed
    if not ids or len(ids)>100:
        raise HTTPException(409,'复测范围为空或超过100份；不能回退全库，请另行整理题目')
    return sorted(ids)


def _chunk(db,document_id,chunk_id):
    row=db.execute('SELECT c.id AS chunk_id,c.document_id,c.page,c.paragraph,c.content,c.line_start,c.line_end,'
        'd.filename,d.sha256,d.version,d.lifecycle,d.status,d.applicability,d.stored_name,d.tags,d.source_metadata FROM chunks c JOIN documents d ON d.id=c.document_id '
        'WHERE c.id=? AND d.id=?',(chunk_id,document_id)).fetchone()
    if row is None or row['status']!='ready':
        raise HTTPException(409,'正确来源已删除、未就绪或不属于所选文档，请重新选择')
    item=dict(row);item['chunk_content_sha256']=text_sha(item['content'])
    metadata=_log_metadata(item)
    item['log_location']=_log_location(item,metadata)
    item['location_kind']=None
    if (_direct_image(item) and all(item[key] is None for key in ('page','paragraph','line_start','line_end'))):
        if db.execute('SELECT 1 FROM ocr_jobs WHERE document_id=?',(document_id,)).fetchone():
            raise HTTPException(409,'直接图文资料存在 OCR 任务，不能用于复测')
        _verify_image_file(item)
        from .image_caption_evidence import ImageCaptionEvidenceError, attach_image_captions
        try:
            selected=attach_image_captions(db,[{'document_id':document_id,'chunk_id':chunk_id}],{document_id})
        except ImageCaptionEvidenceError as exc:
            raise HTTPException(409,'图片说明与已入库片段不一致，不能用于复测') from exc
        if not selected[0].get('_image_caption'):
            raise HTTPException(409,'所选片段并非有效的人工图片说明')
        item['location_kind']='image_caption'
    return item


def prepare(db,original,body):
    sources=[]
    for selected in body.expected_sources:
        item=_chunk(db,selected.document_id,selected.chunk_id)
        for key in ('sha256','version','lifecycle','applicability','chunk_content_sha256'):
            if item[key]!=getattr(selected,key):
                raise HTTPException(409,'选择后来源版本或索引正文发生变化，请重新选择；旧审核保留')
        if selected.excerpt not in item['content']:
            raise HTTPException(422,'正确原文必须是所选真实片段的逐字子串；跨片段请分条选择')
        source={key:item[key] for key in ('document_id','chunk_id','filename','sha256','version','lifecycle','applicability','chunk_content_sha256','page','paragraph')}
        if item['location_kind']:
            source['location_kind']=item['location_kind']
        sources.append(source | item['log_location'] | {'excerpt':selected.excerpt})
    metadata={'expected_sources':sources,'replay_question':body.replay_question,'replay_confirmed':body.replay_confirmed}
    if body.replay_confirmed:
        if any(not _has_location(source) for source in sources):
            raise HTTPException(422,'正确来源缺少有效页码、起始段落、日志行号或图片说明身份，只能保存诊断，不能生成复测题')
        ids=_scope_ids(db,original)
        if any(source['document_id'] not in ids or source['lifecycle']!='active' for source in sources):
            raise HTTPException(422,'正确来源超出原范围或属于历史版本，只能记录诊断；不能扩大同范围复测')
        metadata['scope_documents']=document_manifest(db,ids)
        metadata['scope_basis']='review_time_visible_corpus' if original['document_ids']==[] else 'original_selected_documents'
    return metadata


def validation(db,review):
    reasons=[];original=review['snapshot']
    if review['status'] not in ('confirmed','resolved'):reasons.append('尚未人工确认问题')
    if not original.get('replay_confirmed') or not original.get('replay_question'):
        reasons.append('尚未确认完整独立问题及复测范围')
    if review['should_refuse'] is None:reasons.append('尚未确认资料是否足以回答')
    if not review['expected_answer']:reasons.append('尚未填写期望回答')
    sources=original.get('expected_sources',[])
    if review['should_refuse'] is not None and not review['should_refuse'] and not sources:reasons.append('尚未选择结构化正确来源')
    if review['should_refuse'] and sources:reasons.append('资料不足的复测题不能选择答案支持来源')
    for source in sources:
        if not _has_location(source):reasons.append('正确来源缺少有效页码、起始段落或日志行号')
        try:
            item=_chunk(db,source['document_id'],source['chunk_id'])
            if (any(item[key]!=source[key] for key in ('filename','sha256','version','lifecycle','applicability','chunk_content_sha256','page','paragraph'))
                    or item['location_kind'] != source.get('location_kind')
                    or any(item.get(key)!=source.get(key) for key in ('line_start','line_end'))
                    or source['excerpt'] not in item['content']):
                reasons.append('正确来源版本、位置或索引正文已变化')
            if item['lifecycle']!='active':reasons.append('正确来源已成为历史版本')
        except HTTPException as exc:reasons.append(exc.detail)
    scope=original.get('scope_documents')
    if not scope:reasons.append('尚未冻结复测资料范围')
    else:
        try:
            ids=[doc['document_id'] for doc in scope]
            allowed=acl.visible_document_ids(db,_owner(db,original))
            if allowed is not None and not set(ids)<=allowed:reasons.append('原提问账号的资料权限已变化')
            if document_manifest(db,ids)!=scope:reasons.append('复测范围内资料或索引已变化')
            if any(source['document_id'] not in ids for source in sources):reasons.append('正确来源超出冻结范围')
        except HTTPException as exc:reasons.append(exc.detail)
    return {'ready':not reasons,'reasons':list(dict.fromkeys(reasons)),
            'note':'只校验索引原文/位置一致；不证明OCR质量或答案语义正确'}


def as_case(review):
    original=review['snapshot'];scope=original['scope_documents']
    return {'format':'rag-eval-v2','id':f"feedback-{review['feedback_id']}-review-{review['id']}",
        'question':original['replay_question'],'expected_answer':review['expected_answer'],
        'should_refuse':bool(review['should_refuse']),'expected_sources':original['expected_sources'],
        'context_mode':'standalone','scope_mode':'selected','document_ids':[doc['document_id'] for doc in scope],
        'scope_documents':scope,'human_verified':True,'runnable_eval_case':True,'requires_source_revalidation':False,
        'applicability':review['applicability'],'reviewer':review['reviewer_name'],'reviewed_at':review['created_at'],
        'original':original,'manual_review_required':True}
