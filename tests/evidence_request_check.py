"""In-memory SQLite + in-process HTTP/NDJSON caller checks; no model or port."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.db import SCHEMA, get_db
from app.deps import CurrentUser, require_user
from app.evidence_request import prepare_evidence_request, finish_evidence_request, evidence_events
from app.security import SESSION_COOKIE, hash_session_token


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[StrictInt] | None = None
    purposes: list[Literal["source_excerpt", "current_value", "onsite_remedy"]] | None = None
    stream: bool = False


def database():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); db.executescript(SCHEMA)
    for ident, role, admin in [(1, "root", 0), (2, "user", 0), (3, "user", 0), (4, "user", 1)]:
        db.execute("INSERT INTO users(id,username,password_hash,role,is_kb_admin,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   (ident, f"fixture{ident}", "unused-test-hash", role, admin, "fixture", "fixture"))
        db.execute("INSERT INTO sessions(user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?)",
                   (ident, hash_session_token(f"evidence-fixture-token-{ident}"), "fixture", "2099-01-01T00:00:00+00:00"))
    for ident, visibility, status in [(1,"shared","ready"),(2,"restricted","ready"),(3,"restricted","ready"),
                                      (4,"shared","failed"),(5,"unexpected","ready")]:
        db.execute("INSERT INTO documents(id,filename,stored_name,content_type,size_bytes,sha256,status,version,visibility,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ident,f"file{ident}.txt",f"stored{ident}.txt","text/plain",10,f"{ident:064x}",status,"v1",visibility,"fixture","fixture"))
    for ident, document, text in [(11,1,"PUBLIC FIRST"),(12,1,"PUBLIC SECOND\nline two"),
                                  (21,2,"PRIVATE USER GRANT"),(31,3,"PRIVATE GROUP GRANT"),
                                  (41,4,"FAILED DOCUMENT SECRET"),(51,5,"UNKNOWN VISIBILITY SECRET")]:
        db.execute("INSERT INTO chunks(id,document_id,seq,page,paragraph,token_count,content,vector) VALUES(?,?,?,NULL,1,1,?,?)",
                   (ident,document,ident,text,b"\0\0\0\0"))
    db.execute("INSERT INTO document_acl(document_id,subject_type,subject_id,created_at) VALUES(2,'user',2,'fixture')")
    db.execute("INSERT INTO groups(id,name,created_at,updated_at) VALUES(1,'fixture-group','fixture','fixture')")
    db.execute("INSERT INTO group_members(group_id,user_id,created_at) VALUES(1,2,'fixture')")
    db.execute("INSERT INTO document_acl(document_id,subject_type,subject_id,created_at) VALUES(3,'group',1,'fixture')")
    db.commit(); return db


def create_check_app(db):
    app = FastAPI(); calls = []; state = dict(candidates=[11,12], proposal={"chunk_ids":[12]}, before_finish=None)
    def memory_db():
        yield db
    app.dependency_overrides[get_db] = memory_db
    @app.post("/isolated-evidence")
    def endpoint(body: CheckBody, user: CurrentUser = Depends(require_user), conn=Depends(get_db)):
        try:
            receipt=prepare_evidence_request(conn,user.id,state["candidates"],document_ids=body.document_ids,purposes=body.purposes)
            if receipt.purposes:
                calls.append(body.question); proposal=state["proposal"]
                if state["before_finish"]: state["before_finish"](conn)
            else:
                proposal={"chunk_ids":[]}
            reply=finish_evidence_request(conn,user.id,receipt,proposal)
        except PermissionError:
            raise HTTPException(status_code=403,detail="source_unavailable")
        except ValueError as exc:
            raise HTTPException(status_code=409 if str(exc)=="source_changed" else 422,detail=str(exc))
        if body.stream:
            return StreamingResponse(iter(evidence_events(reply)),media_type="application/x-ndjson",headers={"Cache-Control":"no-store"})
        return reply
    return app,state,calls


def check() -> dict:
    db=database(); app,state,calls=create_check_app(db); passed=[]; observations=[]
    def ok(name, condition):
        assert condition,name
        passed.append(name)
    def set_user(client, ident):
        client.cookies.clear()
        if ident: client.cookies.set(SESSION_COOKIE,f"evidence-fixture-token-{ident}")
    def post(client, *, stream=False, modes=None, **extra):
        body=dict(question="fixture question",purposes=modes if modes is not None else ["source_excerpt"],stream=stream)
        body.update(extra)
        return client.post("/isolated-evidence",json=body)
    def no_leak(response):
        return not any(word in response.text for word in ("PRIVATE", "FAILED DOCUMENT", "UNKNOWN VISIBILITY", "INVENTED"))
    with TestClient(app) as client:
        ok("real_session_required",post(client).status_code==401 and not calls)
        set_user(client,2)
        normal=post(client); data=normal.json()
        ok("shared_excerpt_and_database_identity",normal.status_code==200 and data["sources"][0]["document_sha256"]==f"{1:064x}")
        ok("sparse_citation_identity",data["sources"][0]["source_id"]==2 and "[2] PUBLIC SECOND" in data["answer"] and "PUBLIC FIRST" not in data["answer"])
        streamed=post(client,stream=True); frames=[json.loads(line) for line in streamed.text.splitlines()]
        ok("ndjson_matches_checked_json",streamed.status_code==200 and frames[0]["sources"]==data["sources"]
            and frames[1]["text"]==data["answer"] and frames[2]["status"]==data["status"])
        ok("ndjson_framing_no_inner_newline_split",len(frames)==3 and "line two" in frames[1]["text"])
        for mode in ("current_value","onsite_remedy"):
            result=post(client,modes=[mode]).json()
            ok("document_gap_"+mode,result["status"]=="insufficient" and not result["sources"] and "PUBLIC" not in result["answer"])
        mixed=post(client,modes=["source_excerpt","current_value"]).json()
        ok("mixed_preserves_excerpt_and_gap",mixed["status"]=="partial" and [p["status"] for p in mixed["parts"]]==["source_excerpt","insufficient"])
        mixed_stream=post(client,modes=["source_excerpt","current_value"],stream=True)
        ok("mixed_ndjson_same_gate",json.loads(mixed_stream.text.splitlines()[1])["text"]==mixed["answer"])
        before=len(calls)
        absent=client.post("/isolated-evidence",json={"question":"unknown intent","stream":True})
        absent_frames=[json.loads(line) for line in absent.text.splitlines()]
        ok("missing_mode_clarifies_without_selector",absent_frames[-1]["status"]=="needs_clarification" and not absent_frames[0]["sources"] and len(calls)==before)
        ok("unknown_mode_rejected",post(client,modes=["model_decides"]).status_code==422)
        ok("empty_modes_rejected",post(client,modes=[]).status_code==422)
        ok("duplicate_modes_rejected",post(client,modes=["source_excerpt","source_excerpt"]).status_code==422)
        ok("client_receipt_or_identity_forbidden",post(client,packet={"origin":"live_telemetry"},user_id=1).status_code==422)
        for proposal in ({"chunk_ids":[12],"answer":"INVENTED"},{"chunk_ids":[12],"purpose":"current_value"},{"chunk_ids":[21]}):
            state["proposal"]=proposal
            reply=post(client,stream=True)
            ok("unsafe_model_output_precedes_no_delta_"+str(len(passed)),reply.status_code==422 and no_leak(reply) and '"type": "delta"' not in reply.text)
        state["proposal"]={"chunk_ids":[]}
        empty=post(client).json(); ok("empty_selection_has_no_sources",empty["status"]=="insufficient" and not empty["sources"])
        state["candidates"]=[]
        empty=post(client).json(); ok("empty_candidates_no_global_fallback",empty["status"]=="insufficient" and not empty["sources"])
        state["candidates"]=[21]; state["proposal"]={"chunk_ids":[21]}
        ok("direct_user_grant",post(client).json()["status"]=="source_excerpt")
        outside=post(client,document_ids=[1],stream=True)
        ok("authorized_document_outside_explicit_scope",outside.status_code==403 and no_leak(outside))
        state["candidates"]=[31]; state["proposal"]={"chunk_ids":[31]}
        ok("group_grant",post(client).json()["status"]=="source_excerpt")
        set_user(client,3); before=len(calls)
        denied=post(client,stream=True)
        ok("no_access_is_not_unrestricted",denied.status_code==403 and no_leak(denied) and len(calls)==before)
        state["candidates"]=[11]; state["proposal"]={"chunk_ids":[11]}
        db.execute("UPDATE documents SET visibility='restricted' WHERE id=1")
        before=len(calls); none_visible=post(client,stream=True)
        ok("empty_acl_set_never_means_all_documents",none_visible.status_code==403 and no_leak(none_visible) and len(calls)==before)
        db.execute("UPDATE documents SET visibility='shared' WHERE id=1")
        denied=post(client,document_ids=[2])
        ok("explicit_inaccessible_scope",denied.status_code==403 and no_leak(denied))
        state["candidates"]=[51]; state["proposal"]={"chunk_ids":[51]}
        ok("unknown_visibility_not_shared",post(client).status_code==403)
        state["candidates"]=[21]; state["proposal"]={"chunk_ids":[21]}
        for ident,name in ((1,"root_access"),(4,"logical_kb_admin_access")):
            set_user(client,ident); ok(name,post(client).json()["status"]=="source_excerpt")
        state["candidates"]=[41]; state["proposal"]={"chunk_ids":[41]}
        ok("failed_document_denied_to_admin",post(client).status_code==403)
        set_user(client,2); state["candidates"]=[21]; state["proposal"]={"chunk_ids":[21]}
        state["before_finish"]=lambda conn:conn.execute("DELETE FROM document_acl WHERE document_id=2")
        denied=post(client,stream=True)
        ok("user_grant_revoked_before_first_delta",denied.status_code==403 and no_leak(denied))
        state["candidates"]=[31]; state["proposal"]={"chunk_ids":[31]}
        state["before_finish"]=lambda conn:conn.execute("DELETE FROM group_members WHERE user_id=2")
        ok("group_grant_revoked_before_output",post(client,stream=True).status_code==403)
        set_user(client,4); state["candidates"]=[21]; state["proposal"]={"chunk_ids":[21]}
        state["before_finish"]=lambda conn:conn.execute("UPDATE users SET is_kb_admin=0 WHERE id=4")
        ok("admin_role_revocation_rechecked",post(client,stream=True).status_code==403)
        state["before_finish"]=None; set_user(client,2); state["candidates"]=[11]; state["proposal"]={"chunk_ids":[11]}
        state["before_finish"]=lambda conn:conn.execute("UPDATE chunks SET content='CHANGED' WHERE id=11")
        changed=post(client,stream=True)
        ok("source_content_changed_before_output",changed.status_code==409 and "CHANGED" not in changed.text and "PUBLIC" not in changed.text)
        state["before_finish"]=lambda conn:conn.execute("UPDATE documents SET version='v2' WHERE id=1")
        ok("document_version_changed_before_output",post(client,stream=True).status_code==409)
        state["before_finish"]=lambda conn:conn.execute("UPDATE users SET is_active=0 WHERE id=2")
        ok("disabled_during_selection",post(client,stream=True).status_code==403)
        state["before_finish"]=None; before=len(calls)
        ok("disabled_real_session_dependency",post(client).status_code==403 and len(calls)==before)
        db.execute("UPDATE users SET is_active=1 WHERE id=2")
        receipt=prepare_evidence_request(db,2,[11],purposes=["source_excerpt"])
        root_receipt=prepare_evidence_request(db,1,[11],purposes=["source_excerpt"])
        for ident,bound,name in ((3,receipt,"receipt_not_reusable_by_another_user"),(True,root_receipt,"bool_caller_not_user_one")):
            try: finish_evidence_request(db,ident,bound,{"chunk_ids":[11]})
            except PermissionError: passed.append(name)
            else: raise AssertionError(name)
        observations.append(dict(normal_status=normal.status_code,json_status=data["status"],stream_frames=frames,mixed=mixed))
    db.close()
    return dict(passed=True,checks=passed,observations=observations,model_calls=0,ports_started=0,
                database="in-memory synthetic fixture",existing_cookie_and_acl_dependencies=True,
                limits="No production router, retrieval quality, semantic answer scoring, CSRF/rate-limit or browser rendering acceptance.")


if __name__ == "__main__":
    print(json.dumps(check(),ensure_ascii=False,indent=2))
