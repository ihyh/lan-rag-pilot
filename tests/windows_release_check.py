"""Windows release contracts, local documentation links and portable service entry."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main():
    for removed in ("setup_linux.sh", "Dockerfile", "docker-compose.yml", ".dockerignore",
                    "docs/UBUNTU.md", "docs/IT_handover.md", "docs/OFFLINE_PACKAGE.md"):
        assert not (ROOT / removed).exists(), f"obsolete deployment entry: {removed}"
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    for name in listed:
        path = ROOT / name
        if path.suffix != ".md" or not path.is_file():
            continue
        for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            resolved = path.parent / unquote(target.split("#", 1)[0])
            assert resolved.exists(), f"broken documentation link: {name}: {target}"
    setup = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8-sig")
    assert '$defaultModel = "qwen3:4b-instruct"' in setup
    assert '@("pull", $selectedModel)' in setup
    assert 'if (-not (Test-Path -LiteralPath $envFile))' in setup
    sample = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DEEPSEEK_MODEL=qwen3:4b-instruct" in sample
    assert "RAG_HOST=127.0.0.1" in sample
    for entry, target in (("start_windows.cmd", "start_local.ps1"), ("setup_windows.cmd", "setup_windows.ps1")):
        assert target in (ROOT / entry).read_text(encoding="utf-8")
        assert (ROOT / "scripts" / target).is_file()
    # Exercise the actual entry in a synthetic checkout, mocking only the web server.
    with tempfile.TemporaryDirectory(prefix="rag-service-entry-") as directory:
        # Hosted Windows runners may expose TEMP via an 8.3 alias (RUNNER~1).
        project = Path(directory).resolve()
        (project / ".venv/Lib/site-packages").mkdir(parents=True)
        (project / ".env").write_text("RAG_HOST=127.0.0.1\nRAG_PORT=18987\n", encoding="utf-8")
        code = """
import importlib.util, os, pathlib, sys, uvicorn
entry, project = map(pathlib.Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('service_entry', entry)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.__file__ = str(project / 'scripts/run_personal_service.py')
def verify(application, host, port):
    assert application == 'app.main:app' and host == '127.0.0.1' and port == 18987
    assert pathlib.Path.cwd().samefile(project)
    assert str(project / '.venv/Lib/site-packages') in sys.path
    print('ENTRY_OK', flush=True)
uvicorn.run = verify
module.main()
"""
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", code,
                                 str(ROOT / "scripts/run_personal_service.py"), str(project)],
                                capture_output=True, text=True, env=dict(os.environ, RAG_PORT="1"))
        errors = "\n".join(path.read_text(encoding="utf-8") for path in (project / "logs").glob("*.err.log"))
        assert result.returncode == 0, result.stderr + errors
        logs = list((project / "logs").glob("*.out.log"))
        assert len(logs) == 1 and "ENTRY_OK" in logs[0].read_text(encoding="utf-8")
    print("Windows release: removed entries, documentation, 4B defaults and portable service entry passed.")


if __name__ == "__main__":
    main()
