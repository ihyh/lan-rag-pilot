"""生成模型可达性探测：让 /api/ready 真正代表"能回答问题"。

为什么需要：此前 /api/health 与 /api/ready 只看嵌入模型状态，**从不触碰 Ollama**。
于是"探针全绿、用户提问全部超时"完全可能发生——Ollama 没启动、地址写错、
模型没 pull 都会表现为这样，而监控上一切正常。

判定口径（刻意如此，避免误判把可用实例标成不可用）：

- **只有连接失败/超时算不可用**。401/403/404 等任何 HTTP 响应都说明链路与进程是通的，
  只是接口细节不同，不该因此把整个实例判定为未就绪。**但网关错误（502/503/504）例外**：
  它的含义恰恰是"没到达模型服务"，必须算不可用，否则代理会制造"探针绿、提问红"。
- 能解析出模型清单时，**额外校验配置的模型确实存在**——这能覆盖最常见的
  "忘记 ollama pull"。模型名比对不做前缀匹配：`qwen3:1.7b` 与 `qwen3:4b` 是不同模型，
  放宽成前缀匹配会把"模型不存在"误判为正常。
- 结果带 TTL 缓存：健康检查通常每 30 秒一次，没必要每次都真打 Ollama。
- 不自动重试、不自动拉起服务，只如实报告——探测器的职责是暴露问题，不是掩盖它。
- 请求头按需构造：本地部署通常没有 API key，此时**不发** Authorization 头
  （`Bearer ` 是非法头，会让探测在建连前就失败，把正常实例报成不可用）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import httpx

from .config import settings
from .db import now_iso
from .llm import (
    effective_api_key,
    model_service_client,
    model_service_headers,
    model_service_url,
)


@dataclass
class ProbeResult:
    ok: bool
    message: str
    checked_at: str
    cached: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "message": self.message,
            "checked_at": self.checked_at,
            "cached": self.cached,
        }


def _probe_url() -> str:
    """探测地址与真实调用共用同一套拼接逻辑（见 model_service_url）。"""
    return model_service_url("models")


def _model_present(models: list[str], target: str) -> bool:
    """精确比对模型名，只在 `:latest` 省略写法上做等价处理。

    不做前缀匹配：`qwen3:1.7b` 与 `qwen3:4b` 是两个模型，放宽会把缺模型误判为正常。
    """
    normalized = {item.strip() for item in models}
    if target in normalized:
        return True
    base, _, tag = target.partition(":")
    if not tag and f"{base}:latest" in normalized:
        return True
    if tag == "latest" and base in normalized:
        return True
    return False


def _extract_model_ids(payload) -> list[str] | None:
    """从 OpenAI 风格（data[].id）或 Ollama 原生风格（models[].name）里取模型名。

    无法识别时返回 None，表示"跳过模型校验"，而不是判定失败。
    """
    if not isinstance(payload, dict):
        return None
    items = payload.get("data")
    if isinstance(items, list):
        return [str(x["id"]) for x in items if isinstance(x, dict) and x.get("id")]
    items = payload.get("models")
    if isinstance(items, list):
        out: list[str] = []
        for item in items:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
                if name:
                    out.append(str(name))
        return out
    return None


_GATEWAY_ERROR_CODES = frozenset({502, 503, 504})


class LLMProbe:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._result: ProbeResult | None = None
        self._checked_monotonic = 0.0

    def snapshot(self) -> ProbeResult | None:
        """只返回上一次结果，**绝不触发探测**——供 /api/health 这类轻量探针使用。"""
        with self._lock:
            if self._result is None:
                return None
            return ProbeResult(
                self._result.ok, self._result.message, self._result.checked_at, cached=True
            )

    def reset(self) -> None:
        """清空缓存（测试与配置变更后使用）。"""
        with self._lock:
            self._result = None
            self._checked_monotonic = 0.0

    def probe(self, force: bool = False) -> ProbeResult:
        if not settings.llm_probe_enabled:
            return ProbeResult(
                True,
                "未启用生成模型探测（RAG_READY_PROBE_LLM=0）：就绪状态只反映嵌入模型",
                now_iso(),
            )

        with self._lock:
            if (
                not force
                and self._result is not None
                and time.monotonic() - self._checked_monotonic < settings.llm_probe_ttl_s
            ):
                return ProbeResult(
                    self._result.ok,
                    self._result.message,
                    self._result.checked_at,
                    cached=True,
                )

        result = self._do_probe()
        with self._lock:
            self._result = result
            self._checked_monotonic = time.monotonic()
        return result

    def _do_probe(self) -> ProbeResult:
        # key 缺失（含只填了空白）时提问路径会直接抛 llm_auth，所以这里必须判未就绪。
        # 若照"只探测网络"处理，/api/ready 会是绿的而每个提问都失败——正是本次要消灭的
        # 那类假绿。判断与 llm.effective_api_key 共用同一定义，避免两处口径不一致。
        if not effective_api_key():
            return ProbeResult(
                False,
                "未配置 DEEPSEEK_API_KEY（或只填了空白）：提问会直接失败（llm_auth），"
                "故判定为未就绪。连接内网 Ollama 也需要一个非空占位值，"
                "例如在 .env 中设置 DEEPSEEK_API_KEY=ollama。",
                now_iso(),
            )

        url = _probe_url()
        timeout = settings.llm_probe_timeout_s
        try:
            with model_service_client(
                httpx.Timeout(timeout, connect=min(3.0, timeout))
            ) as client:
                response = client.get(url, headers=model_service_headers())
        except httpx.TimeoutException:
            return ProbeResult(
                False,
                f"连接模型服务超时（{timeout:g} 秒）：{url}。"
                "请确认 Ollama 正在运行、地址可达且未被防火墙拦截。",
                now_iso(),
            )
        except httpx.LocalProtocolError as exc:
            # 请求在建连之前就被拒了：本地配置非法，不是 Ollama 的问题。
            # 单独成一类，否则运维会去反复重启一个本来正常的 Ollama。
            return ProbeResult(
                False,
                f"探测请求本身就非法（{exc}），尚未发出：{url}。"
                "请检查 DEEPSEEK_BASE_URL 是否形如 http://主机:端口，"
                "以及 RAG_LLM_API_KEY 是否含空格或换行。",
                now_iso(),
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                False,
                f"无法连接模型服务（{exc.__class__.__name__}）：{url}。"
                "请检查 Ollama 是否运行、DEEPSEEK_BASE_URL 是否正确。",
                now_iso(),
            )

        try:
            payload = response.json()
        except ValueError:
            payload = None
        models = _extract_model_ids(payload)

        # 网关类错误不是"链路可达"的证据，而是"根本没到达模型服务"的证据：
        # 系统/环境代理无法转发本机或内网地址时，正是用 502/503/504 作答。
        # 若照"任何 HTTP 响应都算可达"处理，就会再次出现"探针绿、提问全红"。
        if response.status_code in _GATEWAY_ERROR_CODES:
            return ProbeResult(
                False,
                f"模型服务返回网关错误 HTTP {response.status_code}：{url}。"
                "常见原因是请求被系统/环境代理接管（代理无法转发本机或内网地址），"
                "其次才是前置反向代理不可用。请检查 HTTP_PROXY/HTTPS_PROXY 与操作系统"
                "代理设置；本产品默认不使用代理（RAG_LLM_TRUST_ENV_PROXY=0）。",
                now_iso(),
            )

        if models is None:
            # 接口形状不认识，但已经拿到 HTTP 响应 → 视为可达，只是跳过模型校验。
            return ProbeResult(
                True,
                f"模型服务可达（HTTP {response.status_code}，未能解析模型清单，已跳过模型校验）",
                now_iso(),
            )

        target = settings.deepseek_model
        if _model_present(models, target):
            return ProbeResult(True, f"模型服务可达，已找到模型 {target}", now_iso())

        listed = "、".join(models[:5]) if models else "（清单为空）"
        return ProbeResult(
            False,
            f"模型服务可达，但其中没有配置的模型 {target}；当前可用：{listed}。"
            f"请在该服务上先执行 ollama pull {target}。",
            now_iso(),
        )


llm_probe = LLMProbe()
