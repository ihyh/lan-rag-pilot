"""Verify feedback deletion, access control, stale confirmation, and atomic audit."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('RAG_EMBED_BACKEND', 'mock')
os.environ.setdefault('RAG_SECRET_KEY', 'feedback-delete-test-secret')
os.environ.setdefault('RAG_ROOT_PASSWORD', 'feedback-delete-test-password')

from fastapi.testclient import TestClient
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, current_user_or_none
from app.main import app


def main():
    db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    now = now_iso()
    for uid, name, role, kb in [(1, 'root', 'root', 0), (2, 'user', 'user', 0), (3, 'admin', 'user', 1)]:
        db.execute('INSERT INTO users(id,username,password_hash,role,is_kb_admin,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                   (uid, name, 'x', role, kb, now, now))
    db.execute("INSERT INTO documents(id,filename,stored_name,content_type,size_bytes,sha256,status,num_chunks,created_at,updated_at) VALUES(1,'guide.txt','guide.txt','text/plain',1,?,'ready',1,?,?)", ('a' * 64, now, now))
    db.execute("INSERT INTO chunks(id,document_id,seq,paragraph,token_count,content,vector) VALUES(1,1,0,1,1,'source',X'00')")
    db.execute("INSERT INTO conversations(id,user_id,title,document_ids,created_at,updated_at) VALUES(1,2,'test','[1]',?,?)", (now, now))
    for cid in (1, 2):
        db.execute('INSERT INTO chats(id,user_id,conversation_id,question,answer,created_at) VALUES(?,2,1,?,?,?)',
                   (cid, f'question {cid}', f'answer {cid}', now))
        db.execute("INSERT INTO chat_sources(chat_id,document_id,chunk_id,paragraph,excerpt) VALUES(?,1,1,1,'source')", (cid,))
        db.execute("INSERT INTO feedback(id,chat_id,user_id,rating,comment,created_at) VALUES(?,?,2,?,?,?)",
                   (cid, cid, 'unhelpful' if cid == 1 else 'helpful', f'comment {cid}', now))
    state = {'user': None}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_or_none] = lambda: state['user']
    client = TestClient(app, raise_server_exceptions=False)
    root = CurrentUser(1, 'root', 'root', True)

    def delete(fid, body):
        return client.request('DELETE', f'/api/admin/feedback/{fid}', json=body)

    try:
        state['user'] = root
        current = client.get('/api/admin/feedback/1/review').json()
        first = {'feedback_hash': current['feedback_hash'], 'expected_review_id': 0}
        result = client.patch('/api/admin/feedback/1/review', json=first | {'status': 'confirmed', 'expected_answer': 'corrected'})
        assert result.status_code == 200, result.text
        assert result.json()['review']['id'] == 1
        valid = first | {'expected_review_id': 1}
        for person, expected in [(None, 401), (CurrentUser(2, 'user', 'user', True), 403),
                                 (CurrentUser(3, 'admin', 'kb_admin', True), 403)]:
            state['user'] = person
            response = delete(1, valid)
            assert response.status_code == expected, (person, response.status_code, response.text)
        state['user'] = root
        assert delete(0, valid).status_code == 422
        assert delete(9223372036854775808, valid).status_code == 422
        assert delete(9999, valid).status_code == 404
        assert delete(1, first).status_code == 409, 'a newer review must prevent stale deletion'
        assert delete(1, valid | {'feedback_hash': '0' * 64}).status_code == 409
        db.execute("UPDATE feedback SET comment='changed' WHERE id=1")
        assert delete(1, valid).status_code == 409, 'changed feedback must prevent stale deletion'
        db.execute("UPDATE feedback SET comment='comment 1' WHERE id=1")
        assert db.execute('SELECT COUNT(*) FROM feedback_reviews').fetchone()[0] == 1
        # A failed audit write must roll back both the delete and its cascades.
        with patch('app.routers.admin.audit.log_audit', side_effect=RuntimeError('synthetic audit failure')):
            response = delete(1, valid)
        assert response.status_code == 500, (response.status_code, response.text)
        assert db.execute('SELECT COUNT(*) FROM feedback WHERE id=1').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM feedback_reviews WHERE feedback_id=1').fetchone()[0] == 1
        response = delete(1, valid)
        assert response.status_code == 204, (response.status_code, response.text)
        assert delete(1, valid).status_code == 404
        assert db.execute('SELECT COUNT(*) FROM feedback WHERE id=1').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM feedback_reviews WHERE feedback_id=1').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM feedback WHERE id=2').fetchone()[0] == 1
        for table, count in [('chats', 2), ('conversations', 1), ('chat_sources', 2), ('documents', 1), ('chunks', 1)]:
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == count, table
        assert db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='feedback_delete'").fetchone()[0] == 1
        # Helpful feedback is also deletable, without an artificial review.
        helpful = client.get('/api/admin/feedback/2/review').json()
        response = delete(2, {'feedback_hash': helpful['feedback_hash'], 'expected_review_id': 0})
        assert response.status_code == 204, (response.status_code, response.text)
        assert db.execute('SELECT COUNT(*) FROM chats').fetchone()[0] == 2
        for path in ('/api/admin/feedback.csv', '/api/admin/feedback-regression-candidates.jsonl', '/api/admin/feedback-eval-cases.jsonl'):
            assert client.get(path).status_code == 404, path
        print('feedback deletion, permissions, stale confirmation, atomic audit, and removed exports passed')
    finally:
        app.dependency_overrides.clear()
        client.close()
        db.close()


if __name__ == '__main__':
    main()
