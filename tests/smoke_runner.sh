#!/usr/bin/env bash
# 容器内端到端冒烟：本地 Mock DeepSeek + 应用（mock 嵌入后端，离线可跑）+ api_smoke.py
# 用法（在项目根目录，见 docs/测试手册）：
#   docker run --rm -v "$PWD:/rag" -w /rag rag-dev bash tests/smoke_runner.sh
set -u
cd "$(dirname "$0")/.."

export RAG_EMBED_BACKEND=mock
export RAG_DATA_DIR=/tmp/smoke-data
export RAG_DB_PATH=/tmp/smoke-data/rag.db
export RAG_UPLOAD_DIR=/tmp/smoke-data/uploads
export RAG_MODELS_DIR=/tmp/smoke-data/models
export RAG_ROOT_PASSWORD=fcd123
export RAG_SECRET_KEY=smoke-secret-key-0123456789abcdef
export RAG_QUERIES_PER_MINUTE=10
export RAG_MAX_CONCURRENT_LLM=3
export RAG_TOP_K=5
export DEEPSEEK_API_KEY=mock-key
export DEEPSEEK_BASE_URL=http://127.0.0.1:8099
export DEEPSEEK_MODEL=mock-model
export DEEPSEEK_TIMEOUT_S=1
export RAG_HOST=127.0.0.1
export RAG_PORT=8090

rm -rf "$RAG_DATA_DIR"
mkdir -p "$RAG_DATA_DIR"

python -m uvicorn tests.mock_deepseek:app --host 127.0.0.1 --port 8099 >/tmp/mock.log 2>&1 &
MOCK_PID=$!
python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 >/tmp/app.log 2>&1 &
APP_PID=$!

cleanup() { kill "$MOCK_PID" "$APP_PID" 2>/dev/null || true; }
trap cleanup EXIT

wait_url() {
  local url=$1
  for _ in $(seq 1 120); do
    if python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('$url', timeout=2).status == 200 else 1)" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "服务未就绪: $url" >&2
  tail -n 60 /tmp/app.log 2>/dev/null || true
  return 1
}

wait_url http://127.0.0.1:8099/healthz || exit 1
wait_url http://127.0.0.1:8090/api/health || exit 1
echo "两个服务均已就绪，开始冒烟测试..."
python tests/api_smoke.py
