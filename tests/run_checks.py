"""Public offline regression entry: no private corpus or production service required."""
from __future__ import annotations

import compileall
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_CHECKS = [
    "injections/check_anchors.py", "eval_runner_check.py", "llm_request_check.py",
    "retrieval_quality_check.py", "hybrid_retrieval_check.py", "chunk_integrity_check.py",
    "doc_parsing_check.py", "conversation_scope_check.py", "config_guard_check.py",
    "embed_device_check.py", "parse_limits_check.py", "parse_isolation_check.py",
    "latency_stats_check.py", "document_acl_check.py", "acl_coverage_check.py",
    "llm_probe_check.py", "win_script_encoding_check.py",
    "filename_scope_check.py", "keyword_fallback_priority_check.py",
    "local_evidence_state_check.py", "secs_body_alias_check.py",
    "secs_structure_projection_check.py", "windows_release_check.py",
    "retrieval_eval_check.py",
    "general_lexical_check.py",
    "reranking_check.py",
    "history_retrieval_check.py",
    "ocr_preprocess_check.py",
    "ocr_preprocess_live_check.py",
    "mcp_server_check.py",
]
JS_CHECKS = ["form_modal_check.js", "citations_check.js", "device_scope_check.js", "scope_selection_check.js", "sw_cache_check.js"]


def main() -> int:
    for directory in ("app", "scripts", "tests"):
        if not compileall.compile_dir(ROOT / directory, quiet=1):
            return 1
    commands = [["node", "--check", str(path)] for path in sorted((ROOT / "app/static/js").glob("*.js"))]
    commands += [[sys.executable, "-X", "utf8", str(ROOT / "tests" / name)] for name in PYTHON_CHECKS]
    commands += [["node", str(ROOT / "tests" / name)] for name in JS_CHECKS]
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    output = Path(tempfile.mkdtemp(prefix="rag-checks-"))
    failures = []
    print(f"Logs: {output}", flush=True)
    for number, command in enumerate(commands, 1):
        name = Path(command[-1]).name
        with (output / f"{number:02d}-{name}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        print(f"[{'PASS' if result.returncode == 0 else 'FAIL'}] {name}", flush=True)
        if result.returncode:
            failures.append(name)
            print((output / f"{number:02d}-{name}.log").read_text(encoding="utf-8")[-6000:], flush=True)
    print(f"{len(commands) - len(failures)}/{len(commands)} checks passed; logs: {output}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
