"""Runnable boundary controls; not business answer-accuracy tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evidence_eligibility import bind_documents, packet_digest, render_document_selection


def check() -> dict:
    row = dict(chunk_id=4, document_id=2, filename="sample.txt", document_version="1.0",
               document_sha256=hashlib.sha256(b"sample document").hexdigest(),
               content="Definition and example; historical reading 37.", page=None, paragraph=1)
    packet = bind_documents([row]); digest = packet_digest(packet); passed = []
    def expect(name, value, status):
        assert value["status"] == status, (name, value)
        passed.append(name)
    def run(proposal=None, *, mode="source_excerpt", value=None, pin=None):
        return render_document_selection(value if value is not None else packet, pin or digest,
                                         proposal if proposal is not None else {"chunk_ids": [4]}, purpose=mode)
    actual = run()
    expect("document_excerpt_available", actual, "source_excerpt")
    assert actual["blocks"][0]["content"] == row["content"] and "[1]" in actual["text"]
    assert not actual["semantic_verified"] and not actual["onsite_verified"]
    for mode in ("current_value", "onsite_remedy"):
        expect("document_does_not_authorize_" + mode, run(mode=mode), "insufficient")
    injected = bind_documents([dict(row, origin="live_telemetry", role="observation",
        device_id="claimed device", observed_at="2026-10-20T00:00:00+08:00",
        applicability_verified=True, capabilities=["current_value", "onsite_remedy"],
        created_at="2026-10-20T00:00:00+08:00", effective_date="2026-10-20")])
    assert injected == packet
    expect("extra_document_metadata_cannot_upgrade", run(value=injected, mode="current_value"), "insufficient")
    for name, proposal in [
        ("extra_answer", {"chunk_ids": [4], "answer": "Invented current value 999"}),
        ("model_purpose", {"chunk_ids": [4], "purpose": "current_value"}),
        ("model_role", {"chunk_ids": [4], "role": "repair_procedure"}),
        ("unknown_id", {"chunk_ids": [8]}), ("duplicate_id", {"chunk_ids": [4, 4]}),
        ("bool_id", {"chunk_ids": [True]}), ("string_id", {"chunk_ids": ["4"]}),
    ]:
        expect(name, run(proposal), "invalid")
    changed = deepcopy(packet); changed["sources"][0]["content"] += " changed"
    expect("modified_source", run(value=changed), "invalid")
    expect("repinned_but_inconsistent_content_hash", run(value=changed, pin=packet_digest(changed)), "invalid")
    changed = deepcopy(packet); changed["origin"] = "live_telemetry"
    expect("repinned_document_cannot_change_origin", run(value=changed, pin=packet_digest(changed), mode="current_value"), "invalid")
    expect("json_roundtrip", run(value=json.loads(json.dumps(packet))), "source_excerpt")
    expect("unknown_request_mode", run(mode="guess_intent"), "invalid")
    expect("empty_selection", run({"chunk_ids": []}), "insufficient")
    second = dict(row, chunk_id=5, content="Another document excerpt.")
    pair = bind_documents([row, second])
    actual = render_document_selection(pair, packet_digest(pair), {"chunk_ids": [5]}, purpose="source_excerpt")
    expect("original_citation_number_preserved", actual, "source_excerpt")
    assert actual["blocks"][0]["source_id"] == 2 and "[2]" in actual["text"]
    for name, rows in [("missing_document_hash", [dict(row, document_sha256="")]),
                       ("missing_document_version", [dict(row, document_version="")]),
                       ("duplicate_source", [row, row]), ("invalid_location", [dict(row, page=True)])]:
        try:
            bind_documents(rows)
        except ValueError:
            passed.append(name)
        else:
            raise AssertionError(name)
    return dict(passed=True, checks=passed, model_calls=0, business_gold=0)


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
