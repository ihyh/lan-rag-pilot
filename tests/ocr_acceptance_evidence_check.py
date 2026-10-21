"""OCR 验收证据目录保护：重复运行不得删除上一轮原页/失败输出。"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

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


def load_acceptance_module():
    spec = importlib.util.spec_from_file_location(
        "ocr_acceptance_check_under_test", ROOT / "tests" / "ocr_acceptance_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    acceptance = load_acceptance_module()
    with tempfile.TemporaryDirectory(prefix="ocr-evidence-") as temp:
        base = Path(temp)
        fresh = base / "new-batch"
        check(acceptance.prepare_evidence_dir(fresh), "新证据目录允许运行")
        check(fresh.is_dir(), "新证据目录被创建")

        reused = base / "old-batch"
        reused.mkdir()
        first_failure = reused / "work"
        first_failure.mkdir()
        (first_failure / "first-failed-output.txt").write_text("旧失败证据", encoding="utf-8")
        (reused / "acceptance.json").write_text('{"old": true}', encoding="utf-8")
        check(not acceptance.prepare_evidence_dir(reused), "已存在且非空的证据目录拒绝重跑")
        check((first_failure / "first-failed-output.txt").read_text(encoding="utf-8") == "旧失败证据",
              "拒绝后旧原页/失败输出仍保留")
        check((reused / "acceptance.json").read_text(encoding="utf-8") == '{"old": true}',
              "拒绝后旧汇总仍保留")
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
