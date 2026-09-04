"""集中配置：全部来自环境变量（.env -> compose env_file）。默认值面向局域网试点。

注意：限流、并发闸门与内存向量索引都依赖“单进程”运行，
因此必须以单 uvicorn worker 启动（本工程默认即如此）。
"""
from __future__ import annotations

import math
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[config] 忽略非法整数 {name}={raw!r}，使用默认 {default}")
        return default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[config] 忽略非法数字 {name}={raw!r}，使用默认 {default}")
        return default


def _bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.version = "0.1.0"

        # 服务
        self.host = os.environ.get("RAG_HOST", "0.0.0.0")
        self.port = _int("RAG_PORT", 8088)
        self.public_origin = (os.environ.get("RAG_PUBLIC_ORIGIN") or "").rstrip("/")

        # 目录
        self.data_dir = Path(os.environ.get("RAG_DATA_DIR") or (BASE_DIR / "data"))
        self.db_path = Path(os.environ.get("RAG_DB_PATH") or (self.data_dir / "rag.db"))
        self.upload_dir = Path(os.environ.get("RAG_UPLOAD_DIR") or (self.data_dir / "uploads"))
        self.models_dir = Path(os.environ.get("RAG_MODELS_DIR") or (BASE_DIR / "models"))

        # 安全
        self.secret_key = os.environ.get("RAG_SECRET_KEY") or ""
        self.root_password = os.environ.get("RAG_ROOT_PASSWORD") or ""
        self.cookie_secure = _bool("RAG_COOKIE_SECURE", False)
        self.session_ttl_hours = _int("RAG_SESSION_TTL_HOURS", 168)

        # DeepSeek API（OpenAI 兼容接口）
        self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY") or ""
        self.deepseek_base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        self.deepseek_model = os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"
        self.deepseek_timeout_s = _float("DEEPSEEK_TIMEOUT_S", 60.0)

        # 嵌入模型
        self.embed_backend = (os.environ.get("RAG_EMBED_BACKEND") or "st").strip().lower()
        self.embed_model = os.environ.get("RAG_EMBED_MODEL") or "BAAI/bge-small-zh-v1.5"
        self.embed_dim = 512  # bge-small-zh-v1.5 输出 512 维；mock 后端沿用同一维度

        # 切块 / 检索
        self.chunk_max_tokens = _int("RAG_CHUNK_MAX_TOKENS", 400)
        self.chunk_overlap_tokens = _int("RAG_CHUNK_OVERLAP_TOKENS", 60)
        self.top_k = _int("RAG_TOP_K", 5)
        # 真实嵌入的低相似度拒答阈值；mock 后端测试时由查询层跳过。
        raw_min_score = _float("RAG_MIN_RELEVANCE_SCORE", 0.25)
        self.min_relevance_score = (
            min(1.0, max(0.0, raw_min_score)) if math.isfinite(raw_min_score) else 0.25
        )

        # 限流 / 并发（可在管理界面运行时调整，见 settings 表）
        self.queries_per_minute = _int("RAG_QUERIES_PER_MINUTE", 10)
        self.max_concurrent_llm = _int("RAG_MAX_CONCURRENT_LLM", 3)

        # LLM 输出
        self.llm_max_tokens = _int("RAG_LLM_MAX_TOKENS", 1200)
        self.llm_temperature = _float("RAG_LLM_TEMPERATURE", 0.2)

        # 上传
        self.max_upload_mb = _int("RAG_MAX_UPLOAD_MB", 25)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
