"""Human review revisions; never change original answers or auto-approve correctness."""
import hashlib
import json

from fastapi import HTTPException

from . import audit,eval_sources
from .db import now_iso

STATES=('pending','confirmed','resolved','dismissed')
_FROM=""" FROM feedback f JOIN users u ON u.id=f.user_id JOIN chats c ON c.id=f.chat_id
LEFT JOIN feedback_reviews r ON r.id=(SELECT MAX(id) FROM feedback_reviews WHERE feedback_id=f.id) """
# Compare content, not second-resolution timestamps: identical resubmission stays reviewed,
# but a changed rating/comment/question/answer reopens without erasing review history.
_EFFECTIVE="""CASE WHEN r.id IS NOT NULL
 AND f.rating != 'helpful'
 AND json_extract(r.snapshot,'$.rating') IS f.rating
 AND json_extract(r.snapshot,'$.comment') IS f.comment
 AND json_extract(r.snapshot,'$.question') IS c.question
 AND json_extract(r.snapshot,'$.answer') IS c.answer
 THEN r.status WHEN f.rating = 'helpful' THEN 'not_required' ELSE 'pending' END"""
_COLUMNS='f.id,f.chat_id,f.user_id,u.username,c.question,c.answer,f.rating,f.comment,f.created_at'


def fingerprint(row):
    content={key:row[key] for key in ('id','chat_id','rating','comment','question','answer')}
    return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def list_feedback(db,status=None,limit=50,offset=0):
    where=' WHERE '+_EFFECTIVE+'=?' if status else ''
    params=[status] if status else []
    total=db.execute('SELECT COUNT(*)'+_FROM+where,params).fetchone()[0]
    rows=db.execute('SELECT '+_COLUMNS+',r.id AS review_id,r.feedback_hash AS saved_feedback_hash,'+
                    _EFFECTIVE+' AS review_status,r.failure_type,r.reviewer_name,r.created_at AS reviewed_at'+
                    _FROM+where+' ORDER BY f.id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        saved=item.pop('saved_feedback_hash')
        item['review_stale']=bool(item['rating'] != 'helpful' and item['review_id'] and saved!=fingerprint(row))
        item['feedback_hash']=fingerprint(row)
        items.append(item)
    return {'items':items,'total':total}


def _feedback(db,feedback_id):
    row=db.execute('SELECT '+_COLUMNS+',c.conversation_id,c.turn_index'+_FROM+' WHERE f.id=?',(feedback_id,)).fetchone()
    if row is None:
        raise HTTPException(404,'反馈不存在')
    return row


def snapshot(db,row):
    result=dict(row)
    result['sources']=[dict(source) for source in db.execute(
        'SELECT s.document_id,s.chunk_id,s.page,s.paragraph,s.excerpt,d.filename,d.sha256,d.version,d.lifecycle '
        'FROM chat_sources s JOIN documents d ON d.id=s.document_id WHERE s.chat_id=? ORDER BY s.id',(row['chat_id'],))]
    scope=db.execute('SELECT document_ids FROM conversations WHERE id=?',(row['conversation_id'],)).fetchone()
    result['document_ids']=json.loads(scope[0]) if scope else None
    result['prior_turns']=[dict(turn) for turn in reversed(db.execute(
        'SELECT question,answer FROM chats WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 6',
        (row['conversation_id'],row['chat_id'])).fetchall())]
    result['context_requires_revalidation']=row['turn_index']>1
    return result


def inspect(db,feedback_id):
    row=_feedback(db,feedback_id)
    latest=db.execute('SELECT * FROM feedback_reviews WHERE feedback_id=? ORDER BY id DESC LIMIT 1',(feedback_id,)).fetchone()
    review=dict(latest) if latest else None
    if review:
        review['snapshot']=json.loads(review['snapshot'])
    stale=bool(row['rating'] != 'helpful' and review and review['feedback_hash']!=fingerprint(row))
    source_validation=eval_sources.validation(db,review) if review else {'ready':False,'reasons':['尚无人工审核']}
    if row['rating'] == 'helpful':source_validation={'ready':False,'reasons':['有帮助的反馈无需审核']}
    if stale:source_validation={'ready':False,'reasons':['反馈内容已变化，须重新审核']+source_validation['reasons']}
    return {'feedback':snapshot(db,row),'feedback_hash':fingerprint(row),'review':review,'review_stale':stale,'source_validation':source_validation,
            'review_status':'not_required' if row['rating'] == 'helpful' else 'pending' if stale or not review else review['status'],
            'history':[dict(r) for r in db.execute('SELECT id,status,reviewer_name,created_at,note FROM feedback_reviews WHERE feedback_id=? ORDER BY id DESC LIMIT 10',(feedback_id,))]}


def save(db,feedback_id,body,user,ip):
    db.execute('BEGIN IMMEDIATE')
    try:
        row=_feedback(db,feedback_id)
        if row['rating'] == 'helpful':
            raise HTTPException(409,'有帮助的反馈无需审核')
        latest=db.execute('SELECT MAX(id) FROM feedback_reviews WHERE feedback_id=?',(feedback_id,)).fetchone()[0] or 0
        if body.feedback_hash!=fingerprint(row) or body.expected_review_id!=latest:
            raise HTTPException(409,'反馈或审核已变化，请关闭后重新打开核对；本次未覆盖已有记录')
        original=snapshot(db,row)
        original.update(eval_sources.prepare(db,original,body))
        cur=db.execute('INSERT INTO feedback_reviews (feedback_id,feedback_hash,status,failure_type,expected_answer,evidence,applicability,should_refuse,note,snapshot,reviewed_by,reviewer_name,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (feedback_id,body.feedback_hash,body.status,body.failure_type,body.expected_answer,body.evidence,body.applicability,
             None if body.should_refuse is None else int(body.should_refuse),body.note,json.dumps(original,ensure_ascii=False),user.id,user.username,now_iso()))
        audit.log_audit(db,action='feedback_review',user_id=user.id,username=user.username,
                        detail=f'feedback:{feedback_id} review:{cur.lastrowid} status:{body.status} category:{body.failure_type}',ip=ip)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return inspect(db,feedback_id)


def export_candidates(db):
    rows=db.execute('SELECT r.*'+_FROM+" WHERE "+_EFFECTIVE+" IN ('confirmed','resolved') ORDER BY f.id").fetchall()
    result=[]
    for row in rows:
        result.append({'format':'feedback-regression-candidate-v1','id':f"feedback-{row['feedback_id']}-review-{row['id']}",
            'question':json.loads(row['snapshot'])['question'],'expected_answer':row['expected_answer'],
            'should_refuse':None if row['should_refuse'] is None else bool(row['should_refuse']),'evidence':row['evidence'],'applicability':row['applicability'],
            'failure_type':row['failure_type'],'review_status':row['status'],'review_note':row['note'],
            'reviewer':row['reviewer_name'],'reviewed_at':row['created_at'],'original':json.loads(row['snapshot']),
            'requires_source_revalidation':True,'runnable_eval_case':False})
    return result


def export_cases(db):
    cases=[]
    for row in db.execute('SELECT r.*'+_FROM+" WHERE "+_EFFECTIVE+" IN ('confirmed','resolved') ORDER BY f.id").fetchall():
        review=dict(row);review['snapshot']=json.loads(review['snapshot'])
        if eval_sources.validation(db,review)['ready']:cases.append(eval_sources.as_case(review))
    return cases
