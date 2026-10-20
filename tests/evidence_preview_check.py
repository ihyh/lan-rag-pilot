"""Isolated HTTP/security checks. Synthetic retrieval is not semantic accuracy."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.evidence_preview import COOKIE, PREFIX, create_preview_app
from app.security import SESSION_COOKIE
from evidence_request_check import database


def check():
    passed = []
    calls = []

    def ok(name, condition):
        assert condition, name
        passed.append(name)

    class Index:
        def search(self, vector, **kwargs):
            calls.append(kwargs)
            ids = [(11, 1), (12, 1), (21, 2), (31, 3)]
            scope = kwargs['document_ids']
            return [{'chunk_id': cid} for cid, doc in ids if scope is None or doc in scope]

    with tempfile.TemporaryDirectory(prefix='rag-evidence-preview-') as directory:
        path = Path(directory) / 'isolated.db'
        fixture = database()
        with closing(sqlite3.connect(path)) as db:
            fixture.backup(db)
        fixture.close()
        app = create_preview_app(path, lambda _q: [1.0], Index())
        origin = 'http://127.0.0.1'
        with TestClient(app, base_url=origin) as client:
            def post(route, body, *, headers=None):
                return client.post(PREFIX + route, json=body, headers={'Origin': origin} if headers is None else headers)

            def login(ident):
                client.cookies.clear()
                return post('/session', {'token': f'evidence-fixture-token-{ident}'})

            def search(**extra):
                body = dict(question='fixture', purposes=['source_excerpt'])
                body.update(extra)
                return post('/search', body)

            def render(request, ids, stream=False, **extra):
                return post('/render', dict(request_id=request['request_id'], source_ids=ids, stream=stream, **extra))

            ok('unauthenticated_no_retrieval', search().status_code == 401 and not calls)
            client.cookies.set(SESSION_COOKIE, 'evidence-fixture-token-1')
            ok('production_cookie_ignored', search().status_code == 401 and not calls)
            ok('host_rebinding_rejected', client.get('/', headers={'Host': 'evil.example'}).status_code == 400)
            ok('csrf_origin_required', post('/session', {'token': 'evidence-fixture-token-2'}, headers={}).status_code == 403)
            ok('csrf_foreign_origin_rejected', post('/session', {'token': 'evidence-fixture-token-2'}, headers={'Origin': 'https://evil.example'}).status_code == 403)
            ok('invalid_session_rejected', post('/session', {'token': 'invalid'}).status_code == 401)
            response = login(2)
            ok('separate_httponly_strict_cookie', response.status_code == 200 and COOKIE in response.headers['set-cookie']
               and 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie'])
            docs = client.get(PREFIX + '/documents').json()['documents']
            ok('document_list_authorized_ready_only', [d['id'] for d in docs] == [1, 2, 3])
            request = search().json()
            ok('scope_applied_before_retrieval', calls[-1]['document_ids'] == {1, 2, 3, 4} and calls[-1]['general_lexical'] is False)
            ok('retrieved_sources_bound_in_rank_order', [s['chunk_id'] for s in request['candidates']] == [11, 12, 21, 31])
            data = render(request, [2]).json()
            ok('sparse_source_id_not_array_position', data['sources'][0]['source_id'] == 2 and '[2] PUBLIC SECOND' in data['answer'] and 'PUBLIC FIRST' not in data['answer'])
            frames = [json.loads(line) for line in render(request, [2], True).text.splitlines()]
            ok('buffered_ndjson_equals_json', len(frames) == 3 and frames[0]['sources'] == data['sources']
               and frames[1]['text'] == data['answer'] and frames[2]['status'] == data['status'])
            mixed = search(purposes=['source_excerpt', 'current_value', 'onsite_remedy']).json()
            reply = render(mixed, [2]).json()
            ok('mixed_excerpt_and_two_gaps', reply['status'] == 'partial' and [p['status'] for p in reply['parts']] == ['source_excerpt', 'insufficient', 'insufficient']
               and all(not p['source_ids'] for p in reply['parts'][1:]))
            onsite = search(purposes=['onsite_remedy']).json()
            reply = render(onsite, [2]).json()
            ok('onsite_only_no_source_prose', reply['status'] == 'insufficient' and not reply['sources'] and 'PUBLIC' not in reply['answer'])
            before = len(calls)
            unknown = search(purposes=None).json()
            ok('missing_purpose_no_retrieval', unknown['request_id'] is None and not unknown['candidates'] and len(calls) == before)
            ok('unknown_purpose_rejected', search(purposes=['model_decides']).status_code == 422)
            before = len(calls)
            ok('explicit_unauthorized_scope_no_retrieval', search(document_ids=[5]).status_code == 403 and len(calls) == before)
            ok('scope_boolean_rejected', search(document_ids=[True]).status_code == 422)
            ok('duplicate_source_ids_rejected', render(request, [2, 2], True).status_code == 422)
            ok('unknown_source_ids_rejected_before_delta', render(request, [8], True).status_code == 422)
            ok('caller_receipt_and_purpose_override_rejected', render(request, [2], purposes=['current_value']).status_code == 422)
            ok('empty_selection_no_fallback', render(request, []).json()['sources'] == [])
            login(3)
            ok('receipt_bound_to_session', render(request, [2], True).status_code == 409)
            login(2)
            request = search(document_ids=[2]).json()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DELETE FROM document_acl WHERE document_id=2")
            denied = render(request, [1], True)
            ok('revocation_before_output_no_delta', denied.status_code == 403 and 'delta' not in denied.text)
            request = search(document_ids=[1]).json()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE chunks SET content='changed' WHERE id=11")
            changed = render(request, [2], True)
            ok('changed_unselected_context_rejected', changed.status_code == 409 and 'delta' not in changed.text)
            login(1)
            request = search().json()
            ok('root_unrestricted_preserved', calls[-1]['document_ids'] is None and request['candidates'])
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE users SET is_active=0 WHERE id=1')
            ok('disabled_user_rechecked', render(request, [1], True).status_code == 403)
            login(3)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE documents SET visibility='restricted'")
                db.execute('DELETE FROM document_acl')
            before = len(calls)
            empty = search().json()
            ok('empty_acl_never_becomes_global_search', not empty['candidates'] and len(calls) == before)
            page = client.get('/')
            script = client.get('/preview.js')
            ok('csp_and_no_store', "script-src 'self'" in page.headers['content-security-policy'] and page.headers['cache-control'] == 'no-store')
            ok('literal_dom_only_no_html_parser', 'innerHTML' not in script.text and 'textContent = source.content' in script.text and 'bySourceId' in script.text)
            ok('logout_only_preview_cookie', COOKIE in post('/logout', {}).headers['set-cookie'])
    return {'passed': True, 'checks': passed, 'count': len(passed), 'semantic_retrieval': False}


if __name__ == '__main__':
    result = check()
    print(result['count'], 'preview HTTP controls PASS; synthetic retrieval, not answer accuracy')
