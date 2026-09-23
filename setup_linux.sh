#!/usr/bin/env bash
# 宿主模式一键安装（联网）。企业容器部署请改用 docs/UBUNTU.md 的流程。
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
# 解析旧版 Word 97-2003（.doc）需要 antiword。容器镜像里已安装；宿主模式必须显式提醒，
# 否则用户会遇到“上传 .doc 直接失败”却查不到原因。
if ! command -v antiword >/dev/null 2>&1; then
    echo "WARNING: antiword 未安装，旧版 Word（.doc）将无法解析。" >&2
    echo "         安装命令：sudo apt-get install -y antiword（Debian/Ubuntu）" >&2
    echo "         其余格式（PDF/DOCX/XLSX/TXT/MD）不受影响。" >&2
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
# 与 Dockerfile 保持一致：先装 CPU 版 PyTorch。直接装 requirements.txt 会让 pip 从默认
# PyPI 拉取带 CUDA 的 torch 轮子（体积大数倍），而本项目全程只用 CPU 编码。
"$venv_python" -m pip install --timeout 120 --retries 10 torch \
    --index-url https://download.pytorch.org/whl/cpu
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

# ---------- 真实校验：不再只打印一句“setup completed” ----------
read_env_value() {
    local key=$1 default=${2:-}
    local value
    value="$(grep -E "^${key}=" "$env_file" 2>/dev/null | head -n 1 | cut -d= -f2- || true)"
    printf '%s' "${value:-$default}"
}

verify_setup() {
    local failed=0
    local embed_model ollama_model

    if (cd "$project" && "$venv_python" -c 'import app.main' >/dev/null 2>&1); then
        echo "  [OK]   Python 依赖与应用模块可导入"
    else
        echo "  [FAIL] 无法导入 app.main，依赖未装全或 .venv 损坏" >&2
        failed=1
    fi

    embed_model="$(read_env_value RAG_EMBED_MODEL models/bge-small-zh-v1.5)"
    if [[ -d "$project/$embed_model" ]]; then
        echo "  [OK]   嵌入模型目录存在：$embed_model"
    else
        echo "  [FAIL] 嵌入模型目录不存在：$project/$embed_model" >&2
        failed=1
    fi

    ollama_model="$(read_env_value DEEPSEEK_MODEL qwen3:1.7b)"
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$ollama_model"; then
        echo "  [OK]   Ollama 已存在模型：$ollama_model"
    else
        echo "  [FAIL] Ollama 中没有模型 $ollama_model（先启动 Ollama 并完成 pull）" >&2
        failed=1
    fi

    if [[ "$failed" -ne 0 ]]; then
        return 1
    fi
    return 0
}

if [[ $no_start -eq 1 ]]; then
    echo "[6/6] Verifying setup (未启动服务)..."
    if verify_setup; then
        echo "配置校验通过。未启动服务；运行 ./setup_linux.sh（不带 --no-start）即可启动。"
        exit 0
    fi
    echo "配置校验未通过，请按上面的 [FAIL] 处理后重试。" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
cd "$project"
echo "[6/6] Starting LAN RAG Pilot..."
port="${RAG_PORT:-8088}"
listen_host="${RAG_HOST:-127.0.0.1}"

# 后台启动 + 健康轮询：旧版用 exec 前台启动，脚本无法在启动后确认服务真的可用，
# 用户只能看到“Setup validation completed.”却不知道健康检查是否通过。
"$venv_python" -m uvicorn app.main:app --host "$listen_host" --port "$port" &
app_pid=$!
cleanup() { kill "$app_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

ready=0
probe_reason=""
for _ in $(seq 1 120); do
    if ! kill -0 "$app_pid" 2>/dev/null; then
        break
    fi
    # 探针显式直连回环，不使用任何代理：本应用与模型服务都是本机/内网依赖，
    # 一旦被 http_proxy 之类的环境代理接管，一个完全正常的实例会返回 502，
    # 把验收判成失败（成因与排查见 docs/IT_handover.md 的“代理接管”一节）。
    # 非 200 时把响应体带回 shell：/api/ready 的 503 正文写明断在哪条链路、该怎么修，
    # 只说“未返回 200”等于把最有用的一句诊断丢掉。
    probe_out="$("$venv_python" - "$port" <<'PY' 2>/dev/null || true
import sys
import os
import urllib.error
import urllib.request

port = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    configured_timeout = float(os.environ.get("RAG_READY_PROBE_TIMEOUT_S", "3"))
except ValueError:
    configured_timeout = 3.0
probe_timeout = max(5.0, configured_timeout + 2.0)
for path in ("/api/health", "/api/ready"):
    try:
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=probe_timeout) as resp:
            if resp.status != 200:
                print(f"{path} HTTP {resp.status}")
                raise SystemExit(1)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace").replace("\n", " ")[:300]
        print(f"{path} HTTP {exc.code} {body}")
        raise SystemExit(1)
    except Exception as exc:
        print(f"{path} 无法连接（{exc.__class__.__name__}）")
        raise SystemExit(1)
print("OK")
PY
)"
    if [[ "$probe_out" == "OK" ]]; then
        ready=1
        break
    fi
    probe_reason="$probe_out"
    sleep 1
done

if [[ $ready -ne 1 ]]; then
    echo "启动后健康检查未通过：/api/health 或 /api/ready 未在 120 秒内返回 200。" >&2
    if [[ -n "$probe_reason" ]]; then
        echo "最近一次探测结果：$probe_reason" >&2
    fi
    echo "请检查上方日志（常见原因：嵌入模型未加载、Ollama 未启动、模型未 pull、配置被启动校验拒绝）。" >&2
    exit 1
fi

echo "已就绪：http://127.0.0.1:${port}（/api/health 与 /api/ready 均返回 200）"
echo "按 Ctrl+C 停止服务。"
wait "$app_pid"
