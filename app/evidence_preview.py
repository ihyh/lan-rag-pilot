"""Opt-in, loopback-only source preview. Not mounted by app.main."""
from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import acl
from .deps import current_user_or_none, require_user
from .evidence_request import prepare_evidence_request, finish_evidence_request, evidence_events
from .security import SESSION_COOKIE, hash_session_token

COOKIE = 'rag_evidence_preview'
PREFIX = '/api/preview'
STATIC = Path(__file__).parent / 'static'


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(min_length=1, max_length=128)


class Search(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[StrictInt] = Field(default_factory=list, max_length=100)
    purposes: list[Literal['source_excerpt', 'current_value', 'onsite_remedy']] | None = None


class Render(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=100)
    source_ids: list[StrictInt] = Field(max_length=8)
    stream: bool = False


def create_preview_app(db_path: Path, embed_query, index) -> FastAPI:
    """Caller supplies an isolated DB and ready embedding/index; no startup downloads."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])
    pending = {}
    lock = threading.RLock()

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.method not in ('GET', 'HEAD') and request.headers.get('origin') != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'same_origin_required'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def database():
        db = sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro', uri=True,
                             check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        try:
            yield db
        finally:
            db.close()

    def authenticate(token, db):
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', token):
            raise HTTPException(401, 'preview_session_required')
        # Reuse normal session/role/expiry checks, but never consume the production cookie.
        adapted = Request({'type': 'http', 'headers': [(b'cookie', f'{SESSION_COOKIE}={token}'.encode('ascii'))]})
        return require_user(current_user_or_none(adapted, db))

    def user(request: Request, db=Depends(database)):
        token = request.cookies.get(COOKIE)
        return authenticate(token, db), hash_session_token(token)

    @app.exception_handler(PermissionError)
    async def denied(_request, _exc):
        return JSONResponse({'detail': 'source_unavailable'}, status_code=403)

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409 if str(exc) == 'source_changed' else 422)

    @app.get('/')
    def page():
        return FileResponse(STATIC / 'evidence-preview.html')

    @app.get('/preview.js')
    def javascript():
        return FileResponse(STATIC / 'evidence-preview.js', media_type='application/javascript')

    @app.post(PREFIX + '/session')
    def login(body: Login, db=Depends(database)):
        current = authenticate(body.token, db)
        reply = JSONResponse({'username': current.username})
        reply.set_cookie(COOKIE, body.token, httponly=True, samesite='strict', path=PREFIX)
        return reply

    @app.post(PREFIX + '/logout')
    def logout():
        reply = JSONResponse({'logged_out': True})
        reply.delete_cookie(COOKIE, path=PREFIX)
        return reply

    @app.get(PREFIX + '/documents')
    def documents(identity=Depends(user), db=Depends(database)):
        current, _ = identity
        scope = acl.effective_scope(db, current, [])
        return {'documents': [dict(row) for row in db.execute(
            "SELECT id,filename FROM documents WHERE status='ready' ORDER BY id"
        ) if scope is None or row['id'] in scope]}

    @app.post(PREFIX + '/search')
    def search(body: Search, identity=Depends(user), db=Depends(database)):
        current, session = identity
        if not body.question.strip():
            raise ValueError('empty_question')
        initial = prepare_evidence_request(db, current.id, [], document_ids=body.document_ids, purposes=body.purposes)
        if not initial.purposes:
            reply = finish_evidence_request(db, current.id, initial, {'chunk_ids': []})
            return {'request_id': None, 'candidates': [], 'message': reply['answer']}
        scope = acl.effective_scope(db, current, body.document_ids)
        hits = [] if scope == set() else index.search(
            embed_query(body.question), k=8, document_ids=scope, min_score=0.25,
            query_text=body.question, general_lexical=False,
        )
        receipt = prepare_evidence_request(db, current.id, [h['chunk_id'] for h in hits],
                                           document_ids=body.document_ids, purposes=body.purposes)
        with lock:
            now = time.monotonic()
            for key in list(pending):
                if now - pending[key][0] > 300:
                    del pending[key]
            if len(pending) >= 64:
                del pending[next(iter(pending))]
            ident = secrets.token_urlsafe(24)
            pending[ident] = (now, session, receipt)
        packet = json.loads(receipt.packet_json)
        return {'request_id': ident, 'candidates': [dict(row, source_id=i + 1) for i, row in enumerate(packet['sources'])],
                'message': '仅供选择资料原文；检索排序不代表语义已核验。'}

    @app.post(PREFIX + '/render')
    def render(body: Render, identity=Depends(user), db=Depends(database)):
        current, session = identity
        with lock:
            stored = pending.get(body.request_id)
        if stored is None or time.monotonic() - stored[0] > 300 or stored[1] != session or stored[2].user_id != current.id:
            raise HTTPException(409, 'search_again')
        receipt = stored[2]
        ids = body.source_ids
        if len(set(ids)) != len(ids) or any(i < 1 or i > len(receipt.chunk_ids) for i in ids):
            raise ValueError('invalid_source_ids')
        proposal = {'chunk_ids': [receipt.chunk_ids[i - 1] for i in ids]}
        reply = finish_evidence_request(db, current.id, receipt, proposal)
        if body.stream:
            return StreamingResponse(iter(evidence_events(reply)), media_type='application/x-ndjson')
        return reply

    return app
