"""Offline access and version checks for answer image previews."""
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND'] = 'mock'
os.environ['RAG_SECRET_KEY'] = 'test-secret-0123456789abcdef'
os.environ['RAG_ROOT_PASSWORD'] = 'test-password'

from fastapi.testclient import TestClient
from app.config import settings
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_user
from app.main import app


def main():
    with tempfile.TemporaryDirectory() as folder:
        settings.upload_dir = Path(folder)
        image = settings.upload_dir / 'diagram.png'
        image.write_bytes(b'image fixture')
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(SCHEMA)
        now = now_iso()
        db.execute("INSERT INTO users (id,username,password_hash,role,is_active,created_at,updated_at) VALUES (1,'alice','x','user',1,?,?)", (now, now))
        db.execute("INSERT INTO documents (id,filename,stored_name,content_type,size_bytes,sha256,status,visibility,created_at,updated_at) VALUES (1,'diagram.png','diagram.png','image/png',? ,?,'ready','shared',?,?)", (image.stat().st_size, digest, now, now))
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        url = '/api/documents/1/image?sha256=' + digest
        assert client.get(url).status_code in (401, 403)
        app.dependency_overrides[require_user] = lambda: CurrentUser(1, 'alice', 'user', True)
        assert client.get(url).content == b'image fixture'
        assert client.get('/api/documents/1/image?sha256=' + '0'*64).status_code == 409
        db.execute("UPDATE documents SET visibility='restricted' WHERE id=1")
        assert client.get(url).status_code == 404
        db.execute("INSERT INTO document_acl (document_id,subject_type,subject_id,created_at) VALUES (1,'user',1,?)", (now,))
        assert client.get(url).status_code == 200
        db.execute("DELETE FROM document_acl WHERE document_id=1")
        assert client.get(url).status_code == 404
        db.execute("INSERT INTO groups (id,name,created_at,updated_at) VALUES (1,'reviewers',?,?)", (now, now))
        db.execute("INSERT INTO group_members (group_id,user_id,created_at) VALUES (1,1,?)", (now,))
        db.execute("INSERT INTO document_acl (document_id,subject_type,subject_id,created_at) VALUES (1,'group',1,?)", (now,))
        assert client.get(url).status_code == 200
        db.execute("UPDATE documents SET content_type='application/pdf' WHERE id=1")
        assert client.get(url).status_code == 404
        db.execute("UPDATE documents SET content_type='image/png' WHERE id=1")
        db.execute("UPDATE documents SET lifecycle='archived' WHERE id=1")
        assert client.get(url).status_code == 404
        db.execute("UPDATE documents SET lifecycle='active' WHERE id=1")
        image.write_bytes(b'changed bytes')
        assert client.get(url).status_code == 409
        app.dependency_overrides.clear()
        db.close()
    print('answer image access checks passed')


if __name__ == '__main__':
    main()
