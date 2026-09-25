"""Prove regression checks turn red, working only in a disposable repository copy."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check_anchors import GROUPS, REPO, check_anchors
import inject_encoding

TESTS = {
    "probe": ("tests/llm_probe_check.py", "tests/config_guard_check.py"),
    "isolation": ("tests/parse_isolation_check.py",),
    "latency": ("tests/latency_stats_check.py",),
    "retrieval": ("tests/hybrid_retrieval_check.py",),
    "chunking": ("tests/chunk_integrity_check.py",),
    "encoding": ("tests/win_script_encoding_check.py",),
}


def copy_repository(destination: Path) -> None:
    """Include working-tree review changes, but never ignored runtime data or .git."""
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO
    )
    for item in paths.split(b"\0"):
        if not item:
            continue
        relative = Path(os.fsdecode(item))
        source = REPO / relative
        if source.is_symlink():
            raise RuntimeError(f"Refusing to copy symlink: {relative}")
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # win_script_encoding_check.py asks Git which scripts are tracked.
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "add", "-A"],
        cwd=destination, check=True, stdout=subprocess.DEVNULL,
    )


def run_test(repository: Path, test: str) -> tuple[int, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith(("RAG_", "DEEPSEEK_")) or key.upper() in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "PYTHONPATH",
        }:
            env.pop(key)
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    # A regular file avoids waiting for EOF from a deliberately orphaned child.
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                [sys.executable, "-u", test], cwd=repository, env=env,
                stdout=output, stderr=subprocess.STDOUT, timeout=600,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
        output.seek(0)
        return code, output.read().decode("utf-8", "replace")


def failure_lines(output: str) -> list[str]:
    """Lines that represent a failed check, whatever output style the test uses.

    Most tests print explicit ``[FAIL]`` lines, and those are used exclusively when present so
    phrase matching stays strict. Assert-style tests (e.g. tests/hybrid_retrieval_check.py)
    put the message in a traceback instead; without this fallback their injections would be
    reported as unproven even though the assertion did go red.
    """
    strict = [line.strip() for line in output.splitlines() if "[FAIL]" in line]
    if strict:
        return strict
    markers = ("AssertionError", "Assertion failed", "Traceback (most recent call last)")
    return [line.strip() for line in output.splitlines() if any(m in line for m in markers)]


def run_group(repository: Path, group: str) -> int:
    tests = TESTS[group]
    print(f"\n== {group}: baseline ==", flush=True)
    for test in tests:
        code, output = run_test(repository, test)
        if code:
            print(f"[FAIL] baseline {test} exited {code}\n{output[-2500:]}", flush=True)
            return 1

    cases = GROUPS.get(group, ())
    for label, rel, original, broken, expected in cases:
        if group == "isolation" and label.startswith("I1 ") and os.name != "nt":
            print(f"[SKIP] {label}: Windows taskkill case", flush=True)
            continue
        target = repository / rel
        before = target.read_bytes()
        source = before.decode("utf-8")
        if source.count(original) != 1:
            print(f"[FAIL] stale anchor in snapshot: {label}", flush=True)
            return 1
        test = "tests/config_guard_check.py" if group == "probe" and rel == "app/config.py" else tests[0]
        try:
            target.write_bytes(source.replace(original, broken).encode("utf-8"))
            code, output = run_test(repository, test)
        finally:
            target.write_bytes(before)
        failed = failure_lines(output)
        expected_phrases = (expected,) if isinstance(expected, str) else expected
        hit = code not in (0, 124) and any(
            phrase in line for line in failed for phrase in expected_phrases
        )
        print(f"[{'PASS' if hit else 'FAIL'}] {label} (exit={code})", flush=True)
        if not hit:
            print("\n".join(failed)[-2500:] or output[-2500:], flush=True)
            return 1

    if group == "encoding":
        target = repository / inject_encoding.TARGET
        before = target.read_bytes()
        try:
            target.write_bytes(before[len(inject_encoding.BOM):])
            code, output = run_test(repository, tests[0])
        finally:
            target.write_bytes(before)
        failed = failure_lines(output)
        hit = code not in (0, 124) and any(inject_encoding.EXPECT in line for line in failed)
        print(f"[{'PASS' if hit else 'FAIL'}] encoding BOM removed (exit={code})", flush=True)
        if not hit:
            print("\n".join(failed)[-2500:] or output[-2500:], flush=True)
            return 1

    for test in tests:
        code, output = run_test(repository, test)
        if code:
            print(f"[FAIL] restore {test} exited {code}\n{output[-2500:]}", flush=True)
            return 1
    print(f"[PASS] {group}: restored and green", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=TESTS, action="append", help="run only these groups")
    args = parser.parse_args()
    if not check_anchors():
        return 1
    with tempfile.TemporaryDirectory(prefix="rag-injections-") as temporary:
        snapshot = Path(temporary)
        copy_repository(snapshot)
        for group in args.group or TESTS:
            if run_group(snapshot, group):
                return 1
    print("\nAll selected injections turned red on the expected assertion; source was untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
