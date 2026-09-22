#!/usr/bin/env bash
# 容器/CI 端到端冒烟：mock 模型 + 应用（mock 嵌入后端，离线可跑）+ api_smoke.py，
# 随后重启应用并运行持久化检查（与 smoke_runner.ps1 的阶段保持一致）。
#
# 用法（在项目根目录）：
#   bash tests/smoke_runner.sh
# 可选环境变量：APP_PORT（默认 8090）、MOCK_PORT（默认 8099）
set -u
cd "$(dirname "$0")/.."

APP_PORT="${APP_PORT:-8090}"
MOCK_PORT="${MOCK_PORT:-8099}"

# 每次运行使用独立临时目录：旧版写死 /tmp/smoke-data 并 rm -rf，同机并行运行会
# 互相删数据、并抢占固定的 8090/8099 端口。
RUN_DIR="$(mktemp -d -t rag-smoke-XXXXXX)"

export RAG_EMBED_BACKEND=mock
export RAG_DATA_DIR="$RUN_DIR"
export RAG_DB_PATH="$RUN_DIR/rag.db"
export RAG_UPLOAD_DIR="$RUN_DIR/uploads"
export RAG_MODELS_DIR="$RUN_DIR/models"
export RAG_ROOT_PASSWORD=fcd123
export RAG_SECRET_KEY=smoke-secret-key-0123456789abcdef
export RAG_QUERIES_PER_MINUTE=10
export RAG_MAX_CONCURRENT_LLM=3
export RAG_TOP_K=5
export DEEPSEEK_API_KEY=mock-key
export DEEPSEEK_BASE_URL="http://127.0.0.1:$MOCK_PORT"
export DEEPSEEK_MODEL=mock-model
export DEEPSEEK_TIMEOUT_S=1
export RAG_HOST=127.0.0.1
export RAG_PORT="$APP_PORT"
export RAG_SMOKE_URL="http://127.0.0.1:$APP_PORT"
export RAG_SMOKE_BASELINE="$RUN_DIR/baseline.json"

mkdir -p "$RAG_DATA_DIR"

MOCK_LOG="$RUN_DIR/mock.log"
APP_LOG="$RUN_DIR/app.log"
APP_RESTART_LOG="$RUN_DIR/app-restart.log"

APP_PID=""
python -m uvicorn tests.mock_deepseek:app --host 127.0.0.1 --port "$MOCK_PORT" >"$MOCK_LOG" 2>&1 &
MOCK_PID=$!

cleanup() {
  if [ -n "$APP_PID" ]; then kill "$APP_PID" 2>/dev/null || true; fi
  kill "$MOCK_PID" 2>/dev/null || true
}
trap cleanup EXIT

start_app() {
  local log=$1
  python -m uvicorn app.main:app --host 127.0.0.1 --port "$APP_PORT" >"$log" 2>&1 &
  APP_PID=$!
}

wait_url() {
  local url=$1 log=$2
  for _ in $(seq 1 120); do
    if python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('$url', timeout=2).status == 200 else 1)" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "服务未就绪: $url" >&2
  tail -n 60 "$log" 2>/dev/null || true
  return 1
}

not_ready=0
wait_url "http://127.0.0.1:$MOCK_PORT/healthz" "$MOCK_LOG" || not_ready=1
start_app "$APP_LOG"
wait_url "http://127.0.0.1:$APP_PORT/api/health" "$APP_LOG" || not_ready=1
if [ "$not_ready" -ne 0 ]; then
  echo "SMOKE_EXIT=2"
  exit 2
fi

echo "两个服务均已就绪，开始冒烟测试..."
if ! python tests/api_smoke.py; then
  code=$?
  echo "SMOKE_EXIT=$code"
  exit "$code"
fi

echo "重启应用以验证持久化..."
kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
start_app "$APP_RESTART_LOG"
if ! wait_url "http://127.0.0.1:$APP_PORT/api/health" "$APP_RESTART_LOG"; then
  echo "SMOKE_EXIT=2"
  exit 2
fi

python tests/persistence_check.py
code=$?
echo "SMOKE_EXIT=$code"
echo "RUN_DIR=$RUN_DIR"
exit "$code"
