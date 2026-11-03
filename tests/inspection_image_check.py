"""Direct-image descriptions have no pages; other missing locations remain visible."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND'] = 'mock'
os.environ['RAG_SECRET_KEY'] = 'inspection-image-test-secret-0123456789'
os.environ['RAG_ROOT_PASSWORD'] = 'inspection-image-test-password'

from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_kb_admin
from app.main import app


def main():
    with tempfile.TemporaryDirectory() as folder:
        settings.upload_dir = Path(folder)
        db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        now = now_iso()
        for ident, filename, tags in [
            (1, 'diagram.png', ['image_caption_only', 'image_text_direct']),
            (2, 'manual.pdf', []),
            (3, 'other.png', []),
            (4, 'pretend.pdf', ['image_caption_only', 'image_text_direct']),
        ]:
            db.execute('INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,num_chunks,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,?,?,?)',
                       (ident, filename, filename, 'image/png' if filename.endswith('.png') else 'application/pdf', 1,
                        str(ident) * 64, 'ready', json.dumps(tags), now, now))
            db.execute('INSERT INTO chunks (document_id,seq,token_count,content,vector) VALUES (?,0,1,?,?)',
                       (ident, '图说明：示例' if ident == 1 else '示例', b'1'))
        db.execute("UPDATE documents SET visibility='restricted' WHERE id=3")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_kb_admin] = lambda: CurrentUser(1, 'root', 'root', True)
        client = TestClient(app)
        try:
            values = []
            for ident in range(1, 5):
                response = client.get(f'/api/library/documents/{ident}/inspection')
                assert response.status_code == 200, response.text
                values.append(response.json())
            assert values[0]['document']['direct_image_description'] is True
            assert values[0]['document']['source_type'] == 'image'
            assert values[0]['quality']['chunks_without_location'] == 1
            assert not any('缺少页码' in message for message in values[0]['quality']['warnings'])
            for value in values[1:]:
                assert value['document']['direct_image_description'] is False
                assert any('缺少页码' in message for message in value['quality']['warnings'])
            db.execute('UPDATE documents SET tags=? WHERE id=2', (json.dumps(['ocr_human_review_pending']),))
            db.execute("INSERT INTO ocr_jobs (document_id,state,created_at,updated_at) VALUES (3,'failed',?,?)", (now, now))
            response = client.get('/api/library/inspection-summary')
            assert response.status_code == 200
            summary = response.json()
            assert summary['total'] == 4
            assert {item['id'] for item in summary['items']} == {2, 3, 4}
            assert all('部分片段缺少位置' in item['reasons'] for item in summary['items'])
            assert 'OCR 文字待核对' in next(item for item in summary['items'] if item['id'] == 2)['reasons']
            assert 'OCR 处理失败' in next(item for item in summary['items'] if item['id'] == 3)['reasons']
            db.execute("UPDATE chunks SET content=' ' WHERE document_id=1")
            direct = next(item for item in client.get('/api/library/inspection-summary').json()['items'] if item['id'] == 1)
            assert '存在空白片段' in direct['reasons']
            assert '部分片段缺少位置' not in direct['reasons']
            db.execute("UPDATE chunks SET content='图说明：示例' WHERE document_id=1")
            app.dependency_overrides[require_kb_admin] = lambda: CurrentUser(1, 'ordinary', 'user', True)
            summary = client.get('/api/library/inspection-summary').json()
            assert summary['total'] == 3
            assert {item['id'] for item in summary['items']} == {2, 4}
            app.dependency_overrides.pop(require_kb_admin)
            assert client.get('/api/library/inspection-summary').status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()
            db.close()
    print('inspection image checks passed')


if __name__ == '__main__':
    main()
