"""Document-origin capabilities for an isolated source-bound answer path.

Call bind_documents only with server-retrieved, ACL-filtered rows. The caller
keeps the packet digest and request purpose outside model output. This module
does not infer a natural-language question's intent or certify source meaning.
"""
from __future__ import annotations

import hashlib
import json
import re


def packet_digest(packet: dict) -> str:
    return hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def bind_documents(rows: list[dict]) -> dict:
    """Bind exact indexed text. Extra document/model labels grant no capability."""
    sources = []
    seen = set()
    for row in rows:
        chunk_id, document_id = row.get("chunk_id"), row.get("document_id")
        if type(chunk_id) is not int or type(document_id) is not int or chunk_id <= 0 or document_id <= 0:
            raise ValueError("invalid_source_identity")
        if chunk_id in seen:
            raise ValueError("duplicate_chunk_id")
        seen.add(chunk_id)
        content, filename = row.get("content"), row.get("filename")
        version, document_sha = row.get("document_version"), row.get("document_sha256")
        if not isinstance(content, str) or not content.strip() or not isinstance(filename, str) or not filename.strip():
            raise ValueError("missing_source_text_or_filename")
        if (not isinstance(version, str) or not version.strip() or not isinstance(document_sha, str)
                or not re.fullmatch(r"[0-9a-f]{64}", document_sha)):
            raise ValueError("missing_document_identity")
        source = dict(chunk_id=chunk_id, document_id=document_id, filename=filename,
                      document_version=version, document_sha256=document_sha, content=content,
                      content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())
        for key in ("page", "paragraph"):
            value = row.get(key)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("invalid_source_location")
            source[key] = value
        sources.append(source)
    # No role classifier: indexed documents provide excerpts, not live credentials.
    return dict(origin="document_index", capabilities=["source_excerpt"], sources=sources)


def render_document_selection(packet: dict, expected_digest: str, proposal: dict, *, purpose: str) -> dict:
    """Render IDs only; purpose and digest must come from the trusted caller."""
    result = dict(status="invalid", text="来源或证据选择校验未通过。", blocks=[],
                  semantic_verified=False, onsite_verified=False)
    try:
        if packet_digest(packet) != expected_digest or bind_documents(packet["sources"]) != packet:
            raise ValueError("source_packet_changed")
        if (not isinstance(proposal, dict) or set(proposal) != {"chunk_ids"}
                or not isinstance(proposal["chunk_ids"], list)
                or any(type(i) is not int for i in proposal["chunk_ids"])
                or len(set(proposal["chunk_ids"])) != len(proposal["chunk_ids"])):
            raise ValueError("invalid_selection")
        sources = {s["chunk_id"]: s for s in packet["sources"]}
        if not set(proposal["chunk_ids"]) <= set(sources):
            raise ValueError("unknown_source")
        if purpose not in ("source_excerpt", "current_value", "onsite_remedy"):
            raise ValueError("unsupported_request_purpose")
    except (KeyError, TypeError, ValueError) as exc:
        return dict(result, reason=str(exc))
    if purpose != "source_excerpt":
        text = ("现有检索来源是文档记录，缺少可校验的现场设备、采集时间或适用性依据，"
                "无法据此确认当前状态。" if purpose == "current_value" else
                "现有检索来源是文档记录，缺少对当前设备和故障适用性的核验，"
                "无法据此确定现场维修方法。")
        return dict(result, status="insufficient", reason="document_has_no_onsite_credentials", text=text)
    selected = set(proposal["chunk_ids"])
    blocks = [dict(source_id=i, **source) for i, source in enumerate(packet["sources"], 1)
              if source["chunk_id"] in selected]
    if not blocks:
        return dict(result, status="insufficient", reason="no_selected_source", text="未选择可供摘录的资料。")
    text = "以下为资料原文；这些记录本身不代表现场当前状态或已核验的维修方法。\n\n"
    text += "\n\n".join(f"[{b['source_id']}] {b['content']}" for b in blocks)
    return dict(result, status="source_excerpt", reason="document_excerpt_only", text=text, blocks=blocks)
