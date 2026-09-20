#!/usr/bin/env bash
set -euo pipefail
umask 077

project="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv_python="$project/.venv/bin/python"
env_file="$project/.env"
no_start=0

if [[ "${1:-}" == "--no-start" ]]; then
    no_start=1
elif [[ $# -gt 0 ]]; then
    echo "Unknown option: $1" >&2
    exit 2
fi

find_python() {
    local candidate
    for candidate in python3.12 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

echo "[1/6] Checking prerequisites..."
base_python="$(find_python || true)"
if [[ -z "$base_python" ]]; then
    echo "Python 3.12 was not found. Install Python 3.12 with its venv module, then run ./setup_linux.sh again." >&2
    exit 1
fi
if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama was not found. Install it from https://docs.ollama.com/linux, start the service, and run ./setup_linux.sh again." >&2
    exit 1
fi

echo "[2/6] Preparing Python environment..."
if [[ ! -x "$venv_python" ]]; then
    if ! "$base_python" -m venv "$project/.venv"; then
        echo "Could not create .venv. Install the Python 3.12 venv package and run ./setup_linux.sh again." >&2
        exit 1
    fi
fi
if ! "$venv_python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
    echo "The existing .venv does not use Python 3.12. Remove .venv and run ./setup_linux.sh again." >&2
    exit 1
fi
"$venv_python" -m pip install --timeout 120 --retries 10 -r "$project/requirements.txt"
"$venv_python" -m pip check

echo "[3/6] Preparing Ollama model..."
export NO_PROXY="127.0.0.1,localhost"
if ! ollama pull qwen3:1.7b; then
    echo "Ollama could not download qwen3:1.7b. Start Ollama, check its network access, and run ./setup_linux.sh again." >&2
    exit 1
fi

echo "[4/6] Preparing BGE model..."
"$venv_python" "$project/scripts/install_bge.py" --project "$project"

echo "[5/6] Preparing local configuration..."
if [[ ! -f "$env_file" ]]; then
    port="$($venv_python - <<'PY'
import socket

for port in (8088, 18088, 18089, 18090):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
    print(port)
    break
else:
    raise SystemExit("Ports 8088 and 18088-18090 are in use.")
PY
)"
    secret="$($venv_python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    initial_password="$($venv_python -c 'import secrets; print(secrets.token_urlsafe(24))')"
    cat >"$env_file" <<EOF
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen3:1.7b
RAG_EMBED_MODEL=models/bge-small-zh-v1.5
RAG_SECRET_KEY=$secret
RAG_ROOT_PASSWORD=$initial_password
RAG_COOKIE_SECURE=false
RAG_HOST=127.0.0.1
RAG_PORT=$port
RAG_PUBLIC_ORIGIN=http://127.0.0.1:$port
NO_PROXY=127.0.0.1,localhost
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
EOF
    echo "Created .env. Initial account: root"
    echo "Initial password: $initial_password"
else
    echo "Existing .env was preserved."
fi
chmod 600 "$env_file"

if [[ $no_start -eq 1 ]]; then
    echo "[6/6] Setup validation completed."
    exit 0
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
cd "$project"
echo "[6/6] Starting LAN RAG Pilot..."
echo "Open http://127.0.0.1:${RAG_PORT:-8088} after startup. Press Ctrl+C here to stop."
exec "$venv_python" -m uvicorn app.main:app --host "${RAG_HOST:-127.0.0.1}" --port "${RAG_PORT:-8088}"
