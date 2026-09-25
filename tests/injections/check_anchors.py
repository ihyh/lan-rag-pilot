"""Fail when an injection no longer matches the current source exactly once."""
from __future__ import annotations

from pathlib import Path

import inject_chunking
import inject_encoding
import inject_eval
import inject_isolation
import inject_latency
import inject_probe
import inject_retrieval

REPO = Path(__file__).resolve().parents[2]
GROUPS = {
    "probe": inject_probe.INJECTIONS,
    "eval": inject_eval.INJECTIONS,
    "isolation": inject_isolation.INJECTIONS,
    "latency": inject_latency.INJECTIONS,
    "retrieval": inject_retrieval.INJECTIONS,
    "chunking": inject_chunking.INJECTIONS,
}


def check_anchors() -> bool:
    problems = []
    for group, cases in GROUPS.items():
        for label, rel, original, _broken, _expect in cases:
            path = REPO / rel
            count = path.read_text(encoding="utf-8").count(original)
            if count != 1:
                problems.append(f"{group}: {label}: {rel} matched {count} times (expected 1)")

    target = REPO / inject_encoding.TARGET
    if not target.read_bytes().startswith(inject_encoding.BOM):
        problems.append(f"encoding: {inject_encoding.TARGET} is missing its UTF-8 BOM")

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return False
    count = sum(len(cases) for cases in GROUPS.values()) + 1
    print(f"All {count} injection anchors valid (including the BOM).")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if check_anchors() else 1)
