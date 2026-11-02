"""Feedback review saves stay simple; replay still requires verified evidence."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('RAG_EMBED_BACKEND', 'mock')
os.environ.setdefault('RAG_SECRET_KEY', 'feedback-simple-test-secret')
os.environ.setdefault('RAG_ROOT_PASSWORD', 'feedback-simple-test-password')

from fastapi.testclient import TestClient
from app import eval_sources, feedback_review
from app.db import SCHEMA, get_db, now_iso
from app.deps import CurrentUser, require_root
from app.main import app


def main():
    db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(SCHEMA)
    now = now_iso()
    for uid, name, role in ((1, 'root', 'root'), (2, 'user', 'user')):
        db.execute('INSERT INTO users(id,username,password_hash,role,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                   (uid, name, 'x', role, now, now))
    sha = 'a' * 64
    db.execute("INSERT INTO documents(id,filename,stored_name,content_type,size_bytes,sha256,status,num_chunks,created_at,updated_at) VALUES(1,'guide.txt','guide.txt','text/plain',10,?,'ready',1,?,?)", (sha, now, now))
    text = 'The evidence is present.'
    db.execute('INSERT INTO chunks(id,document_id,seq,paragraph,token_count,content,vector) VALUES(1,1,0,1,5,?,X\'00\')', (text,))
    db.execute("INSERT INTO conversations(id,user_id,title,document_ids,created_at,updated_at) VALUES(1,2,'test','[1]',?,?)", (now, now))
    db.execute("INSERT INTO chats(id,user_id,conversation_id,question,answer,created_at) VALUES(1,2,1,'Original question','Original answer',?)", (now,))
    db.execute("INSERT INTO chat_sources(chat_id,document_id,chunk_id,paragraph,excerpt) VALUES(1,1,1,1,?)", (text,))
    db.execute("INSERT INTO feedback(id,chat_id,user_id,rating,comment,created_at) VALUES(1,1,2,'unhelpful','Original feedback',?)", (now,))
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_root] = lambda: CurrentUser(1, 'root', 'root', True)
    client = TestClient(app)
    try:
        original = client.get('/api/admin/feedback/1/review').json()
        base = {'feedback_hash': original['feedback_hash'], 'expected_review_id': 0}

        # A draft is valid with no extra fields, and a confirmed issue needs only the expected answer.
        draft = client.patch('/api/admin/feedback/1/review', json=base | {'status': 'pending', 'note': 'Earlier note'})
        assert draft.status_code == 200, draft.text
        confirmed = client.patch('/api/admin/feedback/1/review', json=base | {
            'expected_review_id': 1, 'status': 'confirmed', 'expected_answer': 'Expected answer'})
        assert confirmed.status_code == 200, confirmed.text
        data = confirmed.json()
        assert data['review']['failure_type'] == '' and data['review']['should_refuse'] is None
        assert data['feedback']['question'] == 'Original question' and data['feedback']['answer'] == 'Original answer'
        assert data['feedback']['comment'] == 'Original feedback' and data['feedback']['sources'][0]['excerpt'] == text
        assert [item['note'] for item in data['history']] == ['', 'Earlier note']

        candidate = feedback_review.export_candidates(db)[0]
        assert candidate['should_refuse'] is None and candidate['runnable_eval_case'] is False
        assert candidate['requires_source_revalidation'] is True
        assert feedback_review.export_cases(db) == []

        # Conditional requirements cannot be bypassed through the API.
        current = base | {'expected_review_id': 2, 'status': 'confirmed', 'expected_answer': 'Expected answer'}
        source = {'document_id': 1, 'chunk_id': 1, 'excerpt': text, 'sha256': sha,
                  'chunk_content_sha256': eval_sources.text_sha(text), 'version': '1.0',
                  'lifecycle': 'active', 'applicability': ''}
        invalid = [
            current | {'expected_answer': ''},
            current | {'status': 'resolved'},
            current | {'status': 'dismissed'},
            current | {'replay_confirmed': True, 'should_refuse': True},
            current | {'replay_confirmed': True, 'replay_question': 'Independent question'},
            current | {'replay_confirmed': True, 'replay_question': 'Independent question', 'should_refuse': False},
            current | {'replay_confirmed': True, 'replay_question': 'Independent question', 'should_refuse': True, 'expected_sources': [source]},
        ]
        for payload in invalid:
            response = client.patch('/api/admin/feedback/1/review', json=payload)
            assert response.status_code == 422, (payload, response.text)
        assert db.execute('SELECT COUNT(*) FROM feedback_reviews').fetchone()[0] == 2
        stale = client.patch('/api/admin/feedback/1/review', json=current | {'expected_review_id': 1})
        assert stale.status_code == 409 and db.execute('SELECT COUNT(*) FROM feedback_reviews').fetchone()[0] == 2

        # An explicitly confirmed source-backed replay remains runnable.
        replay = client.patch('/api/admin/feedback/1/review', json=current | {
            'replay_confirmed': True, 'replay_question': 'Independent question',
            'should_refuse': False, 'expected_sources': [source]})
        assert replay.status_code == 200, replay.text
        cases = feedback_review.export_cases(db)
        assert len(cases) == 1 and cases[0]['runnable_eval_case'] is True
        assert cases[0]['should_refuse'] is False and cases[0]['expected_sources'][0]['excerpt'] == text

        # Source changes invalidate the case; earlier review and original feedback remain intact.
        db.execute('UPDATE chunks SET content=? WHERE id=1', ('Changed evidence',))
        assert feedback_review.export_cases(db) == []
        assert db.execute('SELECT COUNT(*) FROM feedback_reviews').fetchone()[0] == 3
        assert db.execute('SELECT note FROM feedback_reviews WHERE id=1').fetchone()[0] == 'Earlier note'
        assert db.execute('SELECT comment FROM feedback WHERE id=1').fetchone()[0] == 'Original feedback'

        refusal = client.patch('/api/admin/feedback/1/review', json=current | {
            'expected_review_id': 3, 'expected_answer': 'The evidence is insufficient.',
            'replay_confirmed': True, 'replay_question': 'Independent question', 'should_refuse': True})
        assert refusal.status_code == 200, refusal.text
        refusal_case = feedback_review.export_cases(db)[0]
        assert refusal_case['should_refuse'] is True and refusal_case['expected_sources'] == []
        db.execute('UPDATE feedback_reviews SET should_refuse=NULL WHERE id=4')
        assert feedback_review.export_cases(db) == [], 'unknown is not an affirmative decision'
        print('feedback simple review checks passed')
    finally:
        app.dependency_overrides.clear()
        client.close()
        db.close()


if __name__ == '__main__':
    main()
