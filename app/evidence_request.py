"""Isolated document-evidence caller; not mounted on the normal query router.

Authentication and candidate retrieval belong to the caller. Re-read current
user/ACL/source state after selection, then serialize one checked response.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from . import acl
from .deps import CurrentUser, logical_role
from .evidence_eligibility import bind_documents, packet_digest, render_document_selection

PURPOSES = ("source_excerpt", "current_value", "onsite_remedy")


@dataclass(frozen=True)
class EvidenceRequest:
    """Server-held receipt. Never accept or reconstruct one from model/client JSON."""
    user_id: int
    chunk_ids: tuple[int, ...]
    document_ids: tuple[int, ...]
    purposes: tuple[str, ...]
    packet_json: str
    digest: str


def _ids(values, limit: int) -> tuple[int, ...]:
    if (not isinstance(values, (list, tuple)) or len(values) > limit
            or any(type(i) is not int or not 0 < i <= 2**63 - 1 for i in values)
            or len(set(values)) != len(values)):
        raise ValueError("invalid_source_ids")
    return tuple(values)


def _sources(db: sqlite3.Connection, user_id: int, chunks: tuple[int, ...], documents: tuple[int, ...]) -> list[dict]:
    user = db.execute("SELECT id, role, is_kb_admin, is_active FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None or not user["is_active"]:
        raise PermissionError("source_unavailable")
    current = CurrentUser(id=int(user["id"]), username="", role=logical_role(user["role"], bool(user["is_kb_admin"])), is_active=True)
    scope = acl.effective_scope(db, current, list(documents))
    if documents:
        marks = ",".join("?" for _ in documents)
        ready = {r["id"] for r in db.execute(f"SELECT id FROM documents WHERE id IN ({marks}) AND status='ready'", documents)}
        if ready != set(documents) or scope != set(documents):
            raise PermissionError("source_unavailable")
    if not chunks:
        return []
    marks = ",".join("?" for _ in chunks)
    rows = db.execute(
        f"SELECT c.id AS chunk_id,c.document_id,c.page,c.paragraph,c.content,d.filename,"
        f"d.sha256 AS document_sha256,d.version AS document_version "
        f"FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id IN ({marks}) AND d.status='ready'", chunks,
    ).fetchall()
    if len(rows) != len(chunks) or any(scope is not None and r["document_id"] not in scope for r in rows):
        raise PermissionError("source_unavailable")
    by_id = {r["chunk_id"]: dict(r) for r in rows}
    return [by_id[i] for i in chunks]


def prepare_evidence_request(db: sqlite3.Connection, user_id: int, chunk_ids: list[int], *,
                             document_ids: list[int] | None = None, purposes: list[str] | None = None) -> EvidenceRequest:
    if type(user_id) is not int or not 0 < user_id <= 2**63 - 1:
        raise PermissionError("source_unavailable")
    chunks = _ids(chunk_ids, 200)
    documents = _ids(document_ids if document_ids is not None else [], 100)
    if purposes is None:
        modes = ()
    elif (not isinstance(purposes, list) or not purposes or len(purposes) > len(PURPOSES)
          or any(type(p) is not str or p not in PURPOSES for p in purposes)
          or len(set(purposes)) != len(purposes)):
        raise ValueError("unsupported_request_purpose")
    else:
        modes = tuple(purposes)
    packet = bind_documents(_sources(db, user_id, chunks, documents))
    return EvidenceRequest(user_id, chunks, documents, modes,
                           json.dumps(packet, ensure_ascii=False), packet_digest(packet))


def finish_evidence_request(db: sqlite3.Connection, user_id: int, receipt: EvidenceRequest, proposal: dict) -> dict:
    if type(user_id) is not int or user_id != receipt.user_id:
        raise PermissionError("source_unavailable")
    packet = bind_documents(_sources(db, user_id, receipt.chunk_ids, receipt.document_ids))
    if packet_digest(packet) != receipt.digest or packet_digest(json.loads(receipt.packet_json)) != receipt.digest:
        raise ValueError("source_changed")
    if not receipt.purposes:
        return dict(status="needs_clarification", answer="请明确选择资料查询、当前状态确认或现场维修确认；可以同时选择多项。",
                    parts=[], sources=[], semantic_verified=False, onsite_verified=False)
    parts = []; references = {}
    for mode in receipt.purposes:
        result = render_document_selection(packet, receipt.digest, proposal, purpose=mode)
        if result["status"] == "invalid":
            raise ValueError("invalid_evidence_selection")
        ids = []
        for block in result["blocks"]:
            ident = block["source_id"]; ids.append(ident)
            references[ident] = {key: block[key] for key in ("source_id", "chunk_id", "document_id", "filename",
                                                           "document_sha256", "document_version", "page", "paragraph")}
        parts.append(dict(purpose=mode, status=result["status"], text=result["text"], source_ids=ids))
    status = (parts[0]["status"] if len(parts) == 1 else
              "partial" if references else "insufficient")
    return dict(status=status, answer="\n\n".join(p["text"] for p in parts), parts=parts,
                sources=[references[i] for i in sorted(references)], semantic_verified=False, onsite_verified=False)


def evidence_events(reply: dict) -> tuple[str, ...]:
    """Buffer frames from the finished reply; never forward raw model deltas."""
    return tuple(json.dumps(event, ensure_ascii=False) + "\n" for event in (
        {"type": "sources", "sources": reply["sources"]},
        {"type": "delta", "text": reply["answer"]},
        {"type": "done", "status": reply["status"], "semantic_verified": False, "onsite_verified": False},
    ))
