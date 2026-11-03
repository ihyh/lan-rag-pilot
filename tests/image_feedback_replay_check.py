"""Direct image descriptions are reviewable and replayable without inventing pages."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('RAG_EMBED_BACKEND', 'mock')
os.environ.setdefault('RAG_SECRET_KEY', 'image-review-test-secret-0123456789')
os.environ.setdefault('RAG_ROOT_PASSWORD', 'image-review-test-password')
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_kb_admin, require_root
from app.main import app
from app import eval_sources, feedback_review
from app.schemas import FeedbackReviewBody
from scripts.isolated_replay import load_cases, _check_manifest
from scripts.eval_runner import evaluate_case


def expect_error(fn, code=None):
    try:
        fn()
    except HTTPException as exc:
        assert code is None or exc.status_code == code, exc
    else:
        raise AssertionError('expected HTTPException')


def main():
    with tempfile.TemporaryDirectory() as temp:
        settings.upload_dir = Path(temp)
        raw = b'synthetic image bytes'
        digest = hashlib.sha256(raw).hexdigest()
        (settings.upload_dir / 'source.png').write_bytes(raw)
        db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        now = now_iso()
        for uid, name, role in [(1, 'root', 'root'), (2, 'user', 'user')]:
            db.execute('INSERT INTO users(id,username,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                       (uid,name,'x',role,now,now))
        tags = json.dumps(['image_caption_only','image_text_direct'])
        db.execute("INSERT INTO documents(id,filename,stored_name,content_type,size_bytes,sha256,status,num_chunks,tags,caption,visibility,created_at,updated_at) VALUES(1,'diagram.png','source.png','image/png',?,?,'ready',1,?,'连接说明','restricted',?,?)",
                   (len(raw),digest,tags,now,now))
        db.execute("INSERT INTO document_acl(document_id,subject_type,subject_id,created_at) VALUES(1,'user',2,?)",(now,))
        text = '图说明：连接说明'
        db.execute("INSERT INTO chunks(id,document_id,seq,token_count,content,vector) VALUES(1,1,0,5,?,X'00')",(text,))
        db.execute("INSERT INTO conversations(id,user_id,title,document_ids,created_at,updated_at) VALUES(1,2,'test','[1]',?,?)",(now,now))
        db.execute("INSERT INTO chats(id,user_id,conversation_id,question,answer,created_at) VALUES(1,2,1,'连接？','旧答案',?)",(now,))
        db.execute("INSERT INTO feedback(id,chat_id,user_id,rating,created_at) VALUES(1,1,2,'unhelpful',?)",(now,))
        admin = CurrentUser(1,'root','root',True)
        current = feedback_review.inspect(db,1)
        selected = {'document_id':1,'chunk_id':1,'excerpt':'连接说明','sha256':digest,
                    'chunk_content_sha256':eval_sources.text_sha(text),'version':'1.0','lifecycle':'active','applicability':''}
        base = {'feedback_hash':current['feedback_hash'],'expected_review_id':0,'status':'pending',
                'expected_sources':[selected]}
        feedback_review.save(db,1,FeedbackReviewBody(**base),admin,'')
        assert feedback_review.export_cases(db) == []  # no automatic human approval
        body = FeedbackReviewBody(**(base | {'expected_review_id':1,'status':'confirmed','failure_type':'misread',
            'expected_answer':'连接说明','evidence':'已人工核对原图和说明','applicability':'设备示例',
            'should_refuse':False,'replay_question':'设备如何连接？','replay_confirmed':True}))
        result = feedback_review.save(db,1,body,admin,'')
        assert result['source_validation']['ready'] is True
        case = feedback_review.export_cases(db)[0]
        db.execute("UPDATE feedback SET rating='helpful' WHERE id=1")
        assert feedback_review.inspect(db,1)['review_status'] == 'not_required'
        assert not feedback_review.inspect(db,1)['source_validation']['ready']
        assert feedback_review.list_feedback(db,'pending')['total'] == 0
        assert feedback_review.list_feedback(db,'not_required')['total'] == 1
        assert feedback_review.export_candidates(db) == [] and feedback_review.export_cases(db) == []
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_root] = lambda: admin
        test_http = TestClient(app)
        assert test_http.get('/api/admin/feedback?review_status=not_required').json()['total'] == 1
        assert test_http.get('/api/admin/feedback?review_status=pending').json()['total'] == 0
        app.dependency_overrides.clear()
        expect_error(lambda: feedback_review.save(db,1,FeedbackReviewBody(**(base | {'expected_review_id':2})),admin,''),409)
        assert db.execute('SELECT COUNT(*) FROM feedback_reviews WHERE feedback_id=1').fetchone()[0] == 2
        db.execute("UPDATE feedback SET rating='unhelpful', comment='changed' WHERE id=1")
        assert feedback_review.inspect(db,1)['review_status'] == 'pending'
        assert feedback_review.list_feedback(db,'pending')['total'] == 1
        db.execute("UPDATE feedback SET comment=NULL WHERE id=1")
        assert feedback_review.inspect(db,1)['review_status'] == 'confirmed'
        expected = case['expected_sources'][0]
        assert expected['location_kind'] == 'image_caption' and expected['page'] is None and expected['paragraph'] is None
        assert expected['chunk_id'] == 1 and expected['sha256'] == digest
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_root] = lambda: admin
        app.dependency_overrides[require_kb_admin] = lambda: admin
        http = TestClient(app)
        class RealClient:
            def request(self,path,method,payload=None):
                result=http.request(method,path,json=payload)
                result.raise_for_status()
                return result.json()
        assert _check_manifest(RealClient(),case) == case['scope_documents']
        app.dependency_overrides.clear()
        class Client:
            def __init__(self):
                self.inspection = {'document':{'id':1,'tags':tags}, 'items':[{'document_id':1,'chunk_id':1,
                    'sha256':digest,'chunk_content_sha256':eval_sources.text_sha(text),'content':text}]}
            def request(self,path,method,payload=None):
                return {'items':case['scope_documents']} if path == '/api/admin/eval-manifest' else self.inspection
        client=Client()
        assert _check_manifest(client,case) == case['scope_documents']
        client.inspection['document']['tags']='[]'
        try: _check_manifest(client,case)
        except RuntimeError: pass
        else: raise AssertionError('unverified image classification accepted')
        client.inspection['document']['tags']=tags
        client.inspection['items'][0]['content']='其他内容'
        try: _check_manifest(client,case)
        except RuntimeError: pass
        else: raise AssertionError('changed image excerpt accepted')
        client.inspection['items']=[]
        try: _check_manifest(client,case)
        except RuntimeError: pass
        else: raise AssertionError('missing image chunk or later page accepted')
        cases = settings.upload_dir / 'cases.jsonl'
        cases.write_text(json.dumps(case,ensure_ascii=False)+'\n',encoding='utf-8')
        assert load_cases(cases)[0]['expected_sources'][0] == expected
        source = {'document_id':1,'chunk_id':1,'filename':'diagram.png','page':None,'paragraph':None,
                  'source_snapshot':{'source_kind':'image','image_caption_only':True,'document_sha256':digest}}
        response = {'answer':'连接说明 [1]','sources':[source]}
        assert evaluate_case(case,response)['citation_location_hit']
        response['sources'][0]['source_snapshot']['document_sha256'] = '0'*64
        assert not evaluate_case(case,response)['citation_location_hit']
        old = copy.deepcopy(case)
        old['scope_documents'][0]['filename']='guide.pdf'
        old['expected_sources'][0].update(filename='guide.pdf',page=2)
        del old['expected_sources'][0]['location_kind']
        cases.write_text(json.dumps(old,ensure_ascii=False)+'\n',encoding='utf-8')
        assert load_cases(cases)[0]['expected_sources'][0]['page'] == 2
        assert evaluate_case(old,{'answer':'说明 [1]','sources':[{'filename':'guide.pdf','page':2,'chunk_id':1}]})['citation_location_hit']
        for change in ({'page':1}, {'filename':'paper.pdf'}, {'location_kind':'forged'}):
            invalid = copy.deepcopy(case)
            invalid['expected_sources'][0].update(change)
            if 'filename' in change:
                invalid['scope_documents'][0]['filename'] = change['filename']
            cases.write_text(json.dumps(invalid,ensure_ascii=False)+'\n',encoding='utf-8')
            try: load_cases(cases)
            except ValueError: pass
            else: raise AssertionError('invalid source accepted')
        # Revalidation fails closed on revoked permission, changed source bytes, or changed classification.
        db.execute('DELETE FROM document_acl WHERE document_id=1 AND subject_id=2')
        assert feedback_review.export_cases(db) == []
        db.execute("INSERT INTO document_acl(document_id,subject_type,subject_id,created_at) VALUES(1,'user',2,?)",(now,))
        (settings.upload_dir / 'source.png').write_bytes(b'changed')
        assert feedback_review.export_cases(db) == []
        expect_error(lambda: eval_sources.document_manifest(db,[1]),409)
        (settings.upload_dir / 'source.png').write_bytes(raw)
        db.execute("UPDATE documents SET tags='[]' WHERE id=1")
        assert feedback_review.export_cases(db) == []
        db.execute("UPDATE documents SET tags=? WHERE id=1",(tags,))
        db.execute("UPDATE chunks SET content='different' WHERE id=1")
        assert feedback_review.export_cases(db) == []
        db.execute('UPDATE chunks SET content=? WHERE id=1',(text,))
        db.execute("INSERT INTO ocr_jobs(document_id,state,created_at,updated_at) VALUES(1,'ready',?,?)",(now,now))
        assert feedback_review.export_cases(db) == []
        db.execute('DELETE FROM ocr_jobs WHERE document_id=1')
        db.execute("UPDATE documents SET lifecycle='archived' WHERE id=1")
        assert feedback_review.export_cases(db) == []
        db.close()
    print('image feedback replay checks passed')


if __name__ == '__main__':
    main()
