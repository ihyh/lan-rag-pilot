"""评测补证边界：无 hits / 空 scope 必须与生产 retrieve_sources 一样短路。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0


def check(condition: bool, message: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {message}")
    else:
        FAIL += 1
        print(f"  [FAIL] {message}")


def load_evaluator():
    spec = importlib.util.spec_from_file_location(
        "retrieval_eval_under_test", ROOT / "scripts" / "retrieval_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    evaluator = load_evaluator()
    allowed = evaluator.selection_switches_allowed
    check(not allowed([], None), "无 hits 时不允许补证")
    check(not allowed([], [1]), "无 hits 时即使范围非空也不允许补证")
    check(not allowed([{"chunk_id": 1}], []), "空文档范围不允许补证（不得退化成全库）")
    check(not allowed([{"chunk_id": 1}], set()), "空 set 范围不允许补证")
    check(allowed([{"chunk_id": 1}], [1]), "有 hits 且非空范围允许补证")
    check(allowed([{"chunk_id": 1}], None), "有 hits 且全库范围允许补证")

    calls = []

    def attach_literal(db, selected, question, document_ids=None):
        calls.append(("literal", tuple(selected), document_ids))
        return list(selected) + [{"chunk_id": 2}]

    def attach_continuation(db, selected):
        calls.append(("continuation", tuple(selected)))
        return list(selected) + [{"chunk_id": 3}]

    original = [{"chunk_id": 1}]
    result = evaluator.apply_selection_switches(
        original, question="q", scope=[], hits=[{"chunk_id": 1}],
        evidence_completion=True, context_continuation=True, db=object(),
        attach_literal=attach_literal, attach_continuation=attach_continuation,
    )
    check(result == original and not calls, "空 scope 的完整分支不调用任何补证 helper")
    result = evaluator.apply_selection_switches(
        original, question="q", scope=[1], hits=[],
        evidence_completion=True, context_continuation=True, db=object(),
        attach_literal=attach_literal, attach_continuation=attach_continuation,
    )
    check(result == original and not calls, "无 hits 的完整分支不调用任何补证 helper")
    for evidence_on, continuation_on in ((True, True), (False, False)):
        calls.clear()
        result = evaluator.apply_selection_switches(
            original, question="q", scope=[], hits=[{"chunk_id": 1}],
            evidence_completion=evidence_on, context_continuation=continuation_on, db=object(),
            attach_literal=attach_literal, attach_continuation=attach_continuation,
        )
        check(result == original and not calls, f"空 scope 在开关 {evidence_on}/{continuation_on} 下都不补证")
        calls.clear()
        result = evaluator.apply_selection_switches(
            original, question="q", scope=[1], hits=[],
            evidence_completion=evidence_on, context_continuation=continuation_on, db=object(),
            attach_literal=attach_literal, attach_continuation=attach_continuation,
        )
        check(result == original and not calls, f"无 hits 在开关 {evidence_on}/{continuation_on} 下都不补证")
    result = evaluator.apply_selection_switches(
        original, question="q", scope=[1], hits=[{"chunk_id": 1}],
        evidence_completion=True, context_continuation=True, db=object(),
        attach_literal=attach_literal, attach_continuation=attach_continuation,
    )
    check([item["chunk_id"] for item in result] == [1, 2, 3]
          and [name for name, *_ in calls] == ["literal", "continuation"],
          "正常有 hits/非空范围时才按顺序调用补证与续段")
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
