"""集中配置：全部来自环境变量（.env -> compose env_file）。默认值面向局域网试点。

注意：限流、并发闸门与内存向量索引都依赖“单进程”运行，
因此必须以单 uvicorn worker 启动（本工程默认即如此）。
"""
from __future__ import annotations

import ipaddress
import math
import os
import re
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# 允许的“内网”主机名后缀；不在此列且带点的公网域名会被启动校验拒绝。
_INTRANET_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa")


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


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").strip().strip("[]").lower()


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "localhost.localdomain"}


def _is_intranet_host(host: str, trusted: set[str] | None = None) -> bool:
    """判断模型服务主机是否属于内网。

    放行：显式信任列表、回环/私有/链路本地 IP、单标签主机名（如 Docker 服务名
    ``ollama``）、以及 ``.local``/``.internal`` 等内网后缀。
    拒绝：其余带点的域名，例如 ``api.deepseek.com``。
    """
    if not host:
        return False
    if trusted and host in trusted:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        pass
    if "." not in host:
        return True
    return host.endswith(_INTRANET_SUFFIXES)


_EMBED_DEVICE_RE = re.compile(r"^(cpu|mps|cuda|cuda:\d+)$")


def _is_valid_embed_device(value: str) -> bool:
    """校验 RAG_EMBED_DEVICE 的写法；不判断设备在当前机器上是否真的可用。"""
    return bool(_EMBED_DEVICE_RE.match((value or "").strip().lower()))


def effective_key(raw: str | None) -> str:
    """判断"API key 是否算已配置"的**唯一定义**：只有空白同样视为未配置。

    必须在单点定义，否则各处口径会不一致：`if not settings.deepseek_api_key` 认为
    一个空格(" ")是已配置，而请求头构造再 strip 一次就变成空，于是发出
    `Authorization: Bearer `——带尾随空格的请求头是非法 HTTP 头，httpcore 会在建连
    之前抛 LocalProtocolError，上层把它报成"无法连接模型服务"，与网络毫无关系。
    启动校验、请求头构造、提问校验与健康探测都走这个函数。
    """
    return (raw or "").strip()


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

        # 生成模型可达性探测（供 /api/ready 使用）。
        # 此前 /api/ready 只看嵌入模型，从不触碰 Ollama，于是"探针全绿、用户提问
        # 全部超时"完全可能发生，而且从监控上看不出来。
        self.llm_probe_enabled = _bool("RAG_READY_PROBE_LLM", True)
        # 结果缓存时长：健康检查每 30 秒一次，没必要每次都真打 Ollama。
        self.llm_probe_ttl_s = _float("RAG_READY_PROBE_TTL_S", 30.0)
        # 单次探测超时。刻意取得小：本地/内网的 /models 是轻量元数据请求，正常在
        # 毫秒级返回；而 /api/ready 现在会真的探测生成模型，探测耗时会计入编排层
        # 健康检查的预算。默认 3 秒，明显小于仓库 compose 健康检查的内层 8 秒与
        # 外层 10 秒。调大它时必须同步调大健康检查超时，否则会出现"应用正常但容器
        # 反复被判不健康"。
        self.llm_probe_timeout_s = _float("RAG_READY_PROBE_TIMEOUT_S", 3.0)

        # 是否让模型服务请求沿用系统/环境里的 HTTP 代理。**默认关闭**。
        # 模型服务按设计是本机或内网依赖（DEEPSEEK_BASE_URL 多为
        # http://127.0.0.1:11434/v1）。httpx 默认 trust_env=True，会读取
        # HTTP_PROXY 等环境变量；在 Windows 上还会经 urllib 读取注册表里的
        # WinINET 系统代理，而且**不读 ProxyOverride 绕过列表**——即使系统
        # 明确写了"127.* 不走代理"，请求仍会被送到代理，返回 502。
        # 结果极其误导：健康探测把 502 当作"链路可达"而变绿，用户提问却全部失败。
        # 需要经代理访问公网 API 的部署显式打开本项。
        self.llm_trust_env_proxy = _bool("RAG_LLM_TRUST_ENV_PROXY", False)

        # 嵌入模型
        self.embed_backend = (os.environ.get("RAG_EMBED_BACKEND") or "st").strip().lower()
        self.embed_model = os.environ.get("RAG_EMBED_MODEL") or "BAAI/bge-small-zh-v1.5"
        self.embed_dim = 512  # bge-small-zh-v1.5 输出 512 维；mock 后端沿用同一维度
        # 嵌入模型运行设备：cpu | mps | cuda | cuda:<序号>。
        # 默认 CPU 是刻意选择，不是遗留：本机实测检索仅 32~530 ms，GPU 编码对"问答"
        # 几乎无收益；它的价值在批量入库，且会与同机的生成模型争抢显存。
        # 显式写成非 CPU 却不可用时不会静默降级（见 embeddings.device_available）。
        self.embed_device = (os.environ.get("RAG_EMBED_DEVICE") or "cpu").strip().lower()
        # 批量编码大小；GPU 上可调大以提升入库吞吐，CPU 上通常无需改动。
        self.embed_batch_size = _int("RAG_EMBED_BATCH_SIZE", 32)

        # 切块 / 检索
        self.chunk_max_tokens = _int("RAG_CHUNK_MAX_TOKENS", 400)
        self.chunk_overlap_tokens = _int("RAG_CHUNK_OVERLAP_TOKENS", 60)
        # 与 .env.example 保持一致：纯 CPU 机器上 3 条比 5 条快约一半，抽样答案内容相同。
        # 取舍与实测数据见 .env.example 的「检索与切块」说明。
        self.top_k = _int("RAG_TOP_K", 3)
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

        # 解析资源上限：防止压缩炸弹或超大表格把内存与内存索引撑爆。
        # 上传体积上限（25MB）挡不住"解压后几十 GB"的构造文件，因此必须单独限制。
        self.parse_max_uncompressed_mb = _int("RAG_PARSE_MAX_UNCOMPRESSED_MB", 256)
        self.parse_max_zip_entries = _int("RAG_PARSE_MAX_ZIP_ENTRIES", 2000)
        self.parse_max_compression_ratio = _int("RAG_PARSE_MAX_COMPRESSION_RATIO", 200)
        self.parse_max_units = _int("RAG_PARSE_MAX_UNITS", 200_000)
        self.parse_max_text_chars = _int("RAG_PARSE_MAX_TEXT_CHARS", 20_000_000)
        self.parse_max_chunks = _int("RAG_PARSE_MAX_CHUNKS", 50_000)

        # 启动安全校验（见 Settings.validate_or_raise）
        self.allow_insecure_start = _bool("RAG_ALLOW_INSECURE_START", False)
        self.llm_trusted_hosts = {
            item.strip().lower()
            for item in (os.environ.get("RAG_LLM_TRUSTED_HOSTS") or "").split(",")
            if item.strip()
        }

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_uncompressed_bytes(self) -> int:
        return self.parse_max_uncompressed_mb * 1024 * 1024

    def validate_or_raise(self) -> None:
        """启动前校验关键配置；不安全或缺失时拒绝启动（除非显式开启逃生开关）。

        docs/SECURITY_AUDIT.md 第 2、3 条指出：模型地址缺失会回退公网、SESSION 密钥
        缺失只警告不阻止启动，等于把数据边界交给运维的细心程度。这里把两条都改为
        fail-closed：宁可起不来，也不要带着错误边界对外服务。
        """
        problems: list[str] = []

        if not self.secret_key:
            problems.append(
                "未设置 RAG_SECRET_KEY：会话令牌会退化为代码内公开的开发密钥。\n"
                '        生成一个：python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

        if not effective_key(self.deepseek_api_key):
            problems.append(
                "未设置 DEEPSEEK_API_KEY（或只填了空白）：连接内网 Ollama 时也需要一个"
                "非空占位值（例如 ollama）。只填空白会让提问以 llm_auth 失败，"
                "所以在这里就拒绝启动，而不是等到用户提问才发现。"
            )

        llm_host = _host_of(self.deepseek_base_url)
        if not _is_intranet_host(llm_host, self.llm_trusted_hosts):
            problems.append(
                f"DEEPSEEK_BASE_URL 指向非内网地址 {self.deepseek_base_url!r}"
                f"（解析出的主机为 {llm_host or '(空)'}）：检索片段会被发送到内网之外。\n"
                "        改用内网 Ollama 地址（如 http://ollama:11434/v1）；若该主机确实是"
                "受控内网机，请加入 RAG_LLM_TRUSTED_HOSTS。"
            )

        origin = self.public_origin
        if origin.startswith("https://") and not self.cookie_secure:
            problems.append(
                f"RAG_PUBLIC_ORIGIN 是 HTTPS（{origin}）但 RAG_COOKIE_SECURE 为 false："
                "会话 Cookie 可能在明文链路上传输。请设置 RAG_COOKIE_SECURE=true。"
            )
        if origin.startswith("http://") and self.cookie_secure:
            origin_host = _host_of(origin)
            if not _is_loopback_host(origin_host):
                problems.append(
                    f"RAG_PUBLIC_ORIGIN 是 HTTP（{origin}）但 RAG_COOKIE_SECURE 为 true："
                    "浏览器不会回传该 Cookie，登录会陷入循环。"
                )

        # 嵌入设备只校验「字符串是否合法」；"cuda 是否真的可用"留给加载阶段判断，
        # 否则纯 CPU 机器上的合法配置会被这里直接挡死。
        if self.llm_probe_ttl_s < 0:
            problems.append(
                f"RAG_READY_PROBE_TTL_S={self.llm_probe_ttl_s} 不能为负："
                "0 表示每次健康检查都真探测，正数表示缓存该秒数。"
            )
        if self.llm_probe_timeout_s <= 0:
            problems.append(
                f"RAG_READY_PROBE_TIMEOUT_S={self.llm_probe_timeout_s} 必须为正数。"
            )
        if not _is_valid_embed_device(self.embed_device):
            problems.append(
                f"RAG_EMBED_DEVICE={self.embed_device!r} 不是合法设备名："
                "只接受 cpu、mps、cuda、cuda:<序号>（如 cuda:0）。"
            )

        if not 1 <= self.embed_batch_size <= 256:
            problems.append(
                f"RAG_EMBED_BATCH_SIZE={self.embed_batch_size} 超出范围：应为 1~256。"
            )

        # 解析上限必须为正；配成 0 或负数会让所有上传都被拒，属于明显误配。
        parse_limits = {
            "RAG_PARSE_MAX_UNCOMPRESSED_MB": self.parse_max_uncompressed_mb,
            "RAG_PARSE_MAX_ZIP_ENTRIES": self.parse_max_zip_entries,
            "RAG_PARSE_MAX_COMPRESSION_RATIO": self.parse_max_compression_ratio,
            "RAG_PARSE_MAX_UNITS": self.parse_max_units,
            "RAG_PARSE_MAX_TEXT_CHARS": self.parse_max_text_chars,
            "RAG_PARSE_MAX_CHUNKS": self.parse_max_chunks,
        }
        bad_limits = [name for name, value in parse_limits.items() if value <= 0]
        if bad_limits:
            problems.append(
                "以下解析上限必须为正整数，否则任何文件都会被拒绝："
                + "、".join(bad_limits)
            )

        if not problems:
            return

        detail = "\n".join(f"  - {item}" for item in problems)
        if self.allow_insecure_start:
            print(
                "\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "  INSECURE MODE：RAG_ALLOW_INSECURE_START=1，已跳过启动校验\n"
                f"{detail}\n"
                "  以上问题依然存在，仅可用于本机调试，禁止用于共享或企业环境。\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
            )
            return

        raise RuntimeError(
            "启动被拒绝：以下配置不安全或缺失。修复后重新启动，"
            "或仅在确认无风险时设置 RAG_ALLOW_INSECURE_START=1 临时跳过：\n"
            f"{detail}\n"
            "  配置项说明见 docs/IT_handover.md；企业部署见 docs/UBUNTU.md。"
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
