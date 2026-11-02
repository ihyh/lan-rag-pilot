"""Verify direct image/description ingestion, retrieval, editing and rollback."""
import struct
import zlib
import os
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND']='mock'
os.environ['RAG_SECRET_KEY']='direct-image-test-secret-0123456789'
os.environ['RAG_ROOT_PASSWORD']='direct-image-test-password'

from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA,get_db,now_iso
from app.deps import CurrentUser,require_kb_admin,require_user
from app.embeddings import embedding_service
from app.index import vector_index
from app.main import app
from app.routers.query import _snapshot_sources
from app.image_caption_evidence import attach_image_captions


def main():
    with tempfile.TemporaryDirectory() as folder:
        settings.upload_dir=Path(folder)
        db=sqlite3.connect(':memory:',isolation_level=None,check_same_thread=False)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        now=now_iso()
        db.execute("INSERT INTO users (id,username,password_hash,role,is_active,created_at,updated_at) VALUES (1,'root','x','root',1,?,?)",(now,now))
        def png(red):
            def chunk(name,payload):
                return struct.pack('>I',len(payload))+name+payload+struct.pack('>I',zlib.crc32(name+payload))
            return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
                    +chunk(b'IDAT',zlib.compress(bytes([0,red,0,0])))+chunk(b'IEND',b''))
        raw=png(100)
        embedding_service.start()
        vector_index.reload(db)
        app.dependency_overrides[get_db]=lambda: db
        client=TestClient(app)
        url='/api/admin/documents/image-text'
        def upload(name='sample.png',caption='Controller 和 SecsBin 通信 NewTopic123',extra=None,content=raw):
            return client.post(url,data={'caption':caption,'device':'Plus_Pro',**(extra or {})},files={'file':(name,content,'image/png')})
        assert upload().status_code in (401,403)
        app.dependency_overrides[require_kb_admin]=lambda: CurrentUser(1,'root','root',True)
        app.dependency_overrides[require_user]=lambda: CurrentUser(1,'root','root',True)
        assert upload(caption='').status_code==422
        assert upload(caption='说明：').status_code==422
        assert upload(content=b'not an image').status_code==422
        with patch('app.ocr_upload.availability',return_value={'available':False,'message':'OCR down'}):
            result=upload()
        assert result.status_code==201,result.text
        did=result.json()['id']
        assert result.json()['state']=='ready'
        doc=db.execute('SELECT status,device,caption,tags,stored_name FROM documents WHERE id=?',(did,)).fetchone()
        assert doc['status']=='ready' and doc['device']=='Plus_Pro'
        assert 'image_text_direct' in doc['tags'] and 'image_caption_only' in doc['tags']
        assert db.execute('SELECT count(*) FROM ocr_jobs WHERE document_id=?',(did,)).fetchone()[0]==0
        chunks=db.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY seq',(did,)).fetchall()
        assert chunks and 'NewTopic123' in chunks[0]['content']
        search=client.post('/api/library/search',json={'query':'NewTopic123','device':'Plus_Pro'})
        assert search.status_code==200 and search.json()['items'][0]['document_id']==did
        fake_result={'answer':'测试回答 [1]','model':'test','latency_ms':1,'prompt_tokens':1,'completion_tokens':1}
        with patch('app.routers.query.llm_chat',return_value=fake_result):
            answer=client.post('/api/query',json={'question':'Controller 和 SecsBin 通信 NewTopic123','document_ids':[did]})
        assert answer.status_code==200,answer.text
        cited=[source for source in answer.json()['sources'] if source['document_id']==did]
        assert cited and cited[0]['source_snapshot']['image_caption_only'] is True
        stored=db.execute('SELECT excerpt,source_snapshot FROM chat_sources WHERE chat_id=? AND document_id=?',
                          (answer.json()['chat_id'],did)).fetchone()
        assert stored and 'NewTopic123' in stored['excerpt']
        old_excerpt,old_snapshot=stored['excerpt'],stored['source_snapshot']
        sha=json.loads(old_snapshot)['document_sha256']
        picture=client.get(f'/api/documents/{did}/image',params={'sha256':sha})
        assert picture.status_code==200 and picture.content==raw
        assert client.get(f'/api/documents/{did}/image',params={'sha256':'0'*64}).status_code==409
        src={'document_id':did,'chunk_id':chunks[0]['id'],'filename':'sample.png','seq':0,'page':None,'paragraph':None,'content':chunks[0]['content']}
        assert attach_image_captions(db,[src],{did})[0]['_image_caption'] is True
        assert _snapshot_sources(db,[src])[0]['source_snapshot']['image_caption_only'] is True
        assert client.post(f'/api/admin/documents/{did}/reindex').status_code==409
        assert upload().status_code==409
        edit=client.patch(f'/api/admin/documents/{did}/caption',json={'caption':'新的说明 NewTopic456','expected_caption':doc['caption']})
        assert edit.status_code==200,edit.text
        stored=db.execute('SELECT excerpt,source_snapshot,chunk_id FROM chat_sources WHERE chat_id=? AND document_id=?',
                          (answer.json()['chat_id'],did)).fetchone()
        assert (stored['excerpt'],stored['source_snapshot'])==(old_excerpt,old_snapshot)
        assert client.post('/api/library/search',json={'query':'NewTopic456'}).json()['total']==1
        assert client.post('/api/library/search',json={'query':'NewTopic123'}).json()['total']==0
        # Failure after index reload must roll back DB, remove newly created original and restore the index.
        real_reload=vector_index.reload
        calls=[]
        def fail_once(conn):
            calls.append(1)
            if len(calls)==1: raise RuntimeError('simulated index failure')
            real_reload(conn)
        with patch.object(vector_index,'reload',side_effect=fail_once):
            try: upload(name='other.png',content=png(200))
            except RuntimeError: pass
            else: raise AssertionError('index failure must propagate')
        assert db.execute('SELECT count(*) FROM documents').fetchone()[0]==1
        assert len(list(settings.upload_dir.iterdir()))==1
        app.dependency_overrides.clear()
        db.close()
    print('direct image text checks passed')


if __name__=='__main__': main()
