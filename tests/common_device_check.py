"""Isolated checks for common-device filtering and changing an uploaded document's device."""
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND'] = 'mock'
os.environ['RAG_SECRET_KEY'] = 'common-device-test-secret-0123456789'
os.environ['RAG_ROOT_PASSWORD'] = 'common-device-test-password'

from fastapi.testclient import TestClient
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_kb_admin
from app.main import app


def main():
    db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    now = now_iso()
    db.execute("INSERT INTO users (id,username,password_hash,role,is_active,created_at,updated_at) VALUES (1,'root','x','root',1,?,?)", (now,now))
    for doc_id, device in enumerate(('通用','Plus_Pro','LP',''), 1):
        db.execute("INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,device,applicability,created_at,updated_at) VALUES (?,?,?,?,?,?,'ready',?,'原适用范围',?,?)",
                   (doc_id,f'{doc_id}.txt',f'{doc_id}.txt','text/plain',1,str(doc_id)*64,device,now,now))
        db.execute("INSERT INTO chunks (document_id,seq,token_count,content,vector) VALUES (?,0,1,'协议测试',?)", (doc_id,b'\0\0\0\0'))
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    endpoint = '/api/admin/documents/4/device'
    assert client.patch(endpoint,json={'device':'通用','expected_device':''}).status_code in (401,403)
    app.dependency_overrides[require_kb_admin] = lambda: CurrentUser(1,'root','root',True)
    def admin_ids(device):
        response = client.get('/api/admin/documents',params={'device':device})
        assert response.status_code == 200, response.text
        return {item['id'] for item in response.json()['items']}
    def search_ids(device):
        response = client.post('/api/library/search',json={'query':'协议','device':device})
        assert response.status_code == 200, response.text
        return {item['document_id'] for item in response.json()['items']}
    assert admin_ids('Plus_Pro') == search_ids('Plus_Pro') == {1,2}
    assert admin_ids('LP') == search_ids('LP') == {1,3}
    assert admin_ids('通用') == search_ids('通用') == {1}
    assert client.patch(endpoint,json={'device':'错误','expected_device':''}).status_code == 422
    assert client.patch(endpoint,json={'device':'通用','expected_device':'LP'}).status_code == 409
    before = dict(db.execute('SELECT * FROM documents WHERE id=4').fetchone())
    assert client.patch(endpoint,json={'device':'通用','expected_device':''}).status_code == 200
    after = dict(db.execute('SELECT * FROM documents WHERE id=4').fetchone())
    assert after['device'] == '通用'
    assert {k:v for k,v in before.items() if k not in ('device','updated_at')} == {k:v for k,v in after.items() if k not in ('device','updated_at')}
    assert admin_ids('Plus_Pro') == search_ids('Plus_Pro') == {1,2,4}
    assert admin_ids('通用') == search_ids('通用') == {1,4}
    assert client.patch(endpoint,json={'device':'LP','expected_device':''}).status_code == 409
    db.execute("UPDATE documents SET status='parsing' WHERE id=4")
    assert client.patch(endpoint,json={'device':'LP','expected_device':'通用'}).status_code == 409
    assert client.patch('/api/admin/documents/999/device',json={'device':'通用','expected_device':''}).status_code == 404
    app.dependency_overrides.clear()
    db.close()
    print('common device checks passed')


if __name__ == '__main__':
    main()
