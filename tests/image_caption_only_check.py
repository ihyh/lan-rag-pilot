"""Offline test for indexing and editing a human-only image description."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND']='mock'
os.environ['RAG_SECRET_KEY']='caption-only-test-secret-0123456789'
os.environ['RAG_ROOT_PASSWORD']='caption-only-test-password'

from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_kb_admin
from app.embeddings import embedding_service
from app.index import vector_index
from app.main import app


def main():
    with tempfile.TemporaryDirectory() as folder:
        settings.data_dir=Path(folder)
        settings.upload_dir=Path(folder)/'uploads'
        settings.upload_dir.mkdir()
        raw=b'test image bytes'
        (settings.upload_dir/'fixture.png').write_bytes(raw)
        sha=hashlib.sha256(raw).hexdigest()
        token='a'*32+'.json'
        result_dir=settings.data_dir/'ocr-results'/'1'
        result_dir.mkdir(parents=True)
        result={'source':{'kind':'image','pages':1,'sha256':sha},
                'pages':[{'page':1,'status':'failed','text':''}]}
        (result_dir/token).write_text(json.dumps(result),encoding='utf-8')
        db=sqlite3.connect(':memory:',isolation_level=None,check_same_thread=False)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        now=now_iso()
        db.execute("INSERT INTO users (id,username,password_hash,role,is_kb_admin,is_active,created_at,updated_at) VALUES (1,'root','x','root',1,1,?,?)",(now,now))
        db.execute("INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,tags,caption,created_at,updated_at) VALUES (1,'diagram.png','fixture.png','image/png',?,?,'parsing','[]','原说明',?,?)",(len(raw),sha,now,now))
        db.execute("INSERT INTO ocr_jobs (document_id,state,result_name,created_at,updated_at) VALUES (1,'review',?,?,?)",(token,now,now))
        embedding_service.start()
        vector_index.reload(db)
        app.dependency_overrides[get_db]=lambda: db
        client=TestClient(app)
        body={'caption':'Plus_Pro 人工说明 NoOcrTopic123','confirmed':True,'review_token':token}
        endpoint='/api/admin/documents/1/ocr/publish-caption'
        assert client.post(endpoint,json=body).status_code in (401,403)
        app.dependency_overrides[require_kb_admin]=lambda: CurrentUser(1,'root','root',True)
        assert client.post(endpoint,json={**body,'confirmed':False}).status_code==422
        assert client.post(endpoint,json={**body,'review_token':'b'*32+'.json'}).status_code==409
        assert client.post(endpoint,json={**body,'caption':'说明：'}).status_code==422
        (settings.upload_dir/'fixture.png').write_bytes(b'changed')
        assert client.post(endpoint,json=body).status_code!=200
        (settings.upload_dir/'fixture.png').write_bytes(raw)
        result['pages'][0].update(status='ok',text='available')
        (result_dir/token).write_text(json.dumps(result),encoding='utf-8')
        assert client.post(endpoint,json=body).status_code==409
        result['pages'][0].update(status='failed',text='')
        (result_dir/token).write_text(json.dumps(result),encoding='utf-8')
        real_reload=vector_index.reload
        calls=[]
        def fail_once(conn):
            calls.append(1)
            if len(calls)==1:
                raise RuntimeError('index unavailable')
            real_reload(conn)
        with patch.object(vector_index,'reload',side_effect=fail_once):
            try:
                client.post(endpoint,json=body)
            except RuntimeError:
                pass
            else:
                raise AssertionError('index failure must propagate')
        assert db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]==0
        assert db.execute('SELECT status FROM documents WHERE id=1').fetchone()[0]=='parsing'
        response=client.post(endpoint,json=body)
        assert response.status_code==200, response.text
        doc=db.execute('SELECT status,caption,tags FROM documents WHERE id=1').fetchone()
        assert doc['status']=='ready' and doc['caption']==body['caption']
        assert 'image_caption_only' in json.loads(doc['tags']) and 'ocr_image_text_only' not in json.loads(doc['tags'])
        trace={}
        vector_index.search(embedding_service.embed_query('NoOcrTopic123'),5,query_text='NoOcrTopic123',trace=trace)
        assert trace['channels']['technical']
        inspected=client.get('/api/admin/documents/1/ocr').json()
        assert inspected['caption_only'] is True and inspected['caption']==body['caption']
        with patch('app.ocr_upload.availability',return_value={'available':True,'message':'ready'}):
            assert client.post('/api/admin/documents/1/ocr/retry').status_code==409
        edit='/api/admin/documents/1/caption'
        assert client.patch(edit,json={'caption':'','expected_caption':body['caption']}).status_code==422
        assert client.patch(edit,json={'caption':'NewCaption456','expected_caption':body['caption']}).status_code==200
        trace={}
        vector_index.search(embedding_service.embed_query('NewCaption456'),5,query_text='NewCaption456',trace=trace)
        assert trace['channels']['technical']
        assert client.post(endpoint,json=body).status_code==409

        pdf=b'pdf fixture'
        (settings.upload_dir/'fixture.pdf').write_bytes(pdf)
        pdf_sha=hashlib.sha256(pdf).hexdigest()
        db.execute("INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,created_at,updated_at) VALUES (2,'fixture.pdf','fixture.pdf','application/pdf',?,?,'parsing',?,?)",(len(pdf),pdf_sha,now,now))
        db.execute("INSERT INTO ocr_jobs (document_id,state,result_name,created_at,updated_at) VALUES (2,'review',?,?,?)",(token,now,now))
        assert client.post('/api/admin/documents/2/ocr/publish-caption',json=body).status_code==422
        assert db.execute('SELECT COUNT(*) FROM chunks WHERE document_id=2').fetchone()[0]==0

        image2=b'another image fixture'
        (settings.upload_dir/'fixture2.png').write_bytes(image2)
        sha2=hashlib.sha256(image2).hexdigest()
        result_dir2=settings.data_dir/'ocr-results'/'3'
        result_dir2.mkdir(parents=True)
        (result_dir2/token).write_text(json.dumps({'source':{'kind':'image','pages':1,'sha256':sha2},
            'pages':[{'page':1,'status':'ok','text':'OCR confirmed text'}]}),encoding='utf-8')
        db.execute("INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,tags,caption,created_at,updated_at) VALUES (3,'fixture2.png','fixture2.png','image/png',?,?,'parsing','[]','普通图片说明',?,?)",(len(image2),sha2,now,now))
        db.execute("INSERT INTO ocr_jobs (document_id,state,result_name,created_at,updated_at) VALUES (3,'review',?,?,?)",(token,now,now))
        normal=client.post('/api/admin/documents/3/ocr/publish',json={'accepted_suspect_pages':[1],
                           'acknowledge_excluded_pages':False,'review_token':token})
        assert normal.status_code==200,normal.text
        doc3=db.execute('SELECT tags FROM documents WHERE id=3').fetchone()
        assert 'ocr_image_text_only' in json.loads(doc3['tags']) and 'image_caption_only' not in json.loads(doc3['tags'])
        assert db.execute('SELECT COUNT(*) FROM chunks WHERE document_id=3').fetchone()[0]==2
        app.dependency_overrides.clear()
        db.close()
    print('caption-only image checks passed')


if __name__=='__main__':
    main()
