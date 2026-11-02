"""Offline checks for safe image caption updates and preserved OCR evidence."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND']='mock'
os.environ['RAG_SECRET_KEY']='caption-test-secret-0123456789abcdef'
os.environ['RAG_ROOT_PASSWORD']='caption-test-password'

from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_kb_admin
from app.embeddings import embedding_service
from app.index import vector_index
from app.main import app
from app import ocr_upload
from app.chunking import TokenizerAdapter


def main():
    with tempfile.TemporaryDirectory() as folder:
        settings.upload_dir=Path(folder)
        raw=b'test image bytes'
        (settings.upload_dir/'fixture.png').write_bytes(raw)
        db=sqlite3.connect(':memory:',isolation_level=None,check_same_thread=False)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        now=now_iso()
        db.execute("INSERT INTO users (id,username,password_hash,role,is_kb_admin,is_active,created_at,updated_at) VALUES (1,'root','x','root',1,1,?,?)",(now,now))
        old='原来的图片说明 OldTopic777，描述 Host 和 Controller。'
        db.execute("INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,num_chunks,tags,caption,created_at,updated_at) VALUES (1,'diagram.png','fixture.png','image/png',?,?,'ready',2,?,?,?,?)",
                   (len(raw),hashlib.sha256(raw).hexdigest(),json.dumps(['ocr','ocr_image_text_only','ocr_visual_relations_unverified']),old,now,now))
        db.execute("INSERT INTO ocr_jobs (document_id,state,result_name,accepted_pages,created_at,updated_at) VALUES (1,'ready','abc.json','[1]',?,?)",(now,now))
        embedding_service.start()
        old_piece=ocr_upload._caption_pieces(old,TokenizerAdapter(None))[0]
        note='图片OCR文字（仅文字已核对，图表关系和阅读顺序未验证）：\nHost Controller'
        vectors=embedding_service.embed_texts([old_piece.text,note])
        db.execute('INSERT INTO chunks (id,document_id,seq,page,paragraph,token_count,content,vector) VALUES (11,1,0,NULL,NULL,?,?,?)',(old_piece.token_count,old_piece.text,vectors[0].tobytes()))
        db.execute('INSERT INTO chunks (id,document_id,seq,page,paragraph,token_count,content,vector) VALUES (12,1,1,NULL,1,?,?,?)',(10,note,vectors[1].tobytes()))
        db.execute("INSERT INTO chats (id,user_id,question,answer,status,created_at) VALUES (1,1,'q','a','ok',?)",(now,))
        db.execute("INSERT INTO chat_sources (chat_id,document_id,chunk_id,excerpt,source_snapshot) VALUES (1,1,11,'原说明摘录','snapshot')")
        db.execute("INSERT INTO chat_sources (chat_id,document_id,chunk_id,excerpt,source_snapshot) VALUES (1,1,12,'OCR摘录','snapshot')")
        vector_index.reload(db)
        app.dependency_overrides[get_db]=lambda: db
        app.dependency_overrides[require_kb_admin]=lambda: CurrentUser(1,'root','root',True)
        client=TestClient(app)
        app.dependency_overrides.pop(require_kb_admin)
        assert client.patch('/api/admin/documents/1/caption',json={'caption':'unauthorized','expected_caption':old}).status_code in (401,403)
        app.dependency_overrides[require_kb_admin]=lambda: CurrentUser(1,'root','root',True)
        def update(caption,expected):
            return client.patch('/api/admin/documents/1/caption',json={'caption':caption,'expected_caption':expected})
        def chunks():
            return [tuple(row) for row in db.execute('SELECT id,seq,page,paragraph,content,vector FROM chunks ORDER BY seq')]
        before=chunks()
        response=update('新说明：NewTopic888 Plus_Pro 上位机经 HOST 连接',old)
        assert response.status_code==200, response.text
        after=chunks()
        assert after[1][0]==12 and after[1][2:]==before[1][2:]
        assert after[0][4]=='图说明：新说明：NewTopic888 Plus_Pro 上位机经 HOST 连接'
        trace={}
        vector_index.search(embedding_service.embed_query('NewTopic888'),5,query_text='NewTopic888',trace=trace)
        assert after[0][0] in trace['channels']['technical']
        trace={}
        vector_index.search(embedding_service.embed_query('OldTopic777'),5,query_text='OldTopic777',trace=trace)
        assert not trace['channels']['technical']
        sources=db.execute('SELECT chunk_id,excerpt,source_snapshot FROM chat_sources ORDER BY id').fetchall()
        assert tuple(sources[0])==(None,'原说明摘录','snapshot')
        assert tuple(sources[1])==(12,'OCR摘录','snapshot')
        assert update('冲突修改',old).status_code==409
        assert update('x'*2001,response.json()['caption']).status_code==422
        assert chunks()==after
        with patch.object(embedding_service,'embed_texts',side_effect=RuntimeError('embedding failure')):
            try:
                ocr_upload.update_caption(db,1,'失败的新说明',response.json()['caption'],CurrentUser(1,'root','root',True),'')
            except RuntimeError:
                pass
            else:
                raise AssertionError('embedding failure must propagate')
        assert chunks()==after
        assert db.execute('SELECT caption FROM documents WHERE id=1').fetchone()[0]==response.json()['caption']
        reload_real=vector_index.reload
        attempts=[]
        def fail_once(conn):
            attempts.append(1)
            if len(attempts)==1:
                raise RuntimeError('reload failure')
            reload_real(conn)
        with patch.object(vector_index,'reload',side_effect=fail_once):
            try:
                ocr_upload.update_caption(db,1,'索引故障的新说明',response.json()['caption'],CurrentUser(1,'root','root',True),'')
            except RuntimeError:
                pass
            else:
                raise AssertionError('index failure must propagate')
        assert chunks()==after
        assert db.execute('SELECT caption FROM documents WHERE id=1').fetchone()[0]==response.json()['caption']
        long_caption='更多'*500
        assert update(long_caption,response.json()['caption']).status_code==200
        many=chunks()
        assert len(many)>2 and [row[1] for row in many]==list(range(len(many)))
        assert many[-1][0]==12 and many[-1][2:]==before[1][2:]
        assert update('',long_caption).status_code==200
        assert len(chunks())==1 and chunks()[0][0:2]==(12,0)
        db.execute("UPDATE chunks SET content='unexpected' WHERE seq=0")
        assert update('不应写入','').status_code==409
        assert db.execute('SELECT caption FROM documents WHERE id=1').fetchone()[0]==''
        app.dependency_overrides.clear()
        db.close()
    print('image caption checks passed')


if __name__=='__main__':
    main()
