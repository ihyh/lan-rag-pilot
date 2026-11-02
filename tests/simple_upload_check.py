"""Isolated upload checks for the simplified import interface."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND'] = 'mock'
os.environ['RAG_SECRET_KEY'] = 'simple-upload-test-secret-0123456789'
os.environ['RAG_ROOT_PASSWORD'] = 'simple-upload-password'

from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_kb_admin
from app.embeddings import embedding_service
from app.index import vector_index
from app.main import app


def main():
    with tempfile.TemporaryDirectory() as folder:
        settings.upload_dir = Path(folder)
        db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        now=now_iso()
        db.execute("INSERT INTO users (id,username,password_hash,role,is_active,created_at,updated_at) VALUES (1,'root','x','root',1,?,?)",(now,now))
        embedding_service.start()
        vector_index.reload(db)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_kb_admin] = lambda: CurrentUser(1,'root','root',True)
        client = TestClient(app)
        assert client.post('/api/admin/web-import',json={'url':'https://example.com'}).status_code == 404
        assert client.post('/api/admin/web-images/1/prepare',json={'version':'1.0'}).status_code == 404
        assert client.post('/api/admin/documents',files={'file':('test.log',b'log','text/plain')}).status_code == 422
        assert client.post('/api/admin/documents',data={'as_log':'true'},files={'file':('test.txt',b'text','text/plain')}).status_code == 422
        response=client.post('/api/admin/documents',data={'device':'通用'},files={'file':('notes.md','# 设备\n协议测试内容'.encode(),'text/markdown')})
        assert response.status_code == 201, response.text
        doc=db.execute('SELECT id,status,device FROM documents WHERE filename=?',('notes.md',)).fetchone()
        assert doc['status']=='ready' and doc['device']=='通用'
        assert '协议测试内容' in db.execute('SELECT content FROM chunks WHERE document_id=?',(doc['id'],)).fetchone()[0]
        response=client.post('/api/admin/documents',files={'file':('ordinary.txt','普通文本'.encode(),'text/plain')})
        assert response.status_code == 201, response.text
        with patch('app.ocr_upload.availability', return_value={'available':True,'message':'ready'}), \
             patch('app.ocr_upload.run_job'):
            response=client.post('/api/admin/documents/ocr',data={'device':'Plus_Pro','caption':'接口关系'},
                                 files={'file':('figure.png',b'\x89PNG\r\n\x1a\nfixture','image/png')})
        assert response.status_code == 202, response.text
        assert db.execute("SELECT device,caption FROM documents WHERE filename='figure.png'").fetchone()[:] == ('Plus_Pro','接口关系')
        app.dependency_overrides.clear()
        db.close()
    print('simple upload checks passed')


if __name__ == '__main__':
    main()
