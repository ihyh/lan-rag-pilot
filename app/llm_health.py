"""生成模型可达性探测：让 /api/ready 真正代表"能回答问题"。

为什么需要：此前 /api/health 与 /api/ready 只看嵌入模型状态，**从不触碰 Ollama**。
于是"探针全绿、用户提问全部超时"完全可能发生——Ollama 没启动、地址写错、
模型没 pull 都会表现为这样，而监控上一切正常。

判定口径（刻意如此，避免误判把可用实例标成不可用）：

- 连接失败、超时、鉴权/额度/限流错误以及服务端 5xx 都算不可用。404/405 可表示该
  OpenAI 兼容服务没有模型清单端点，此时只确认链路可达并跳过模型清单校验。
- 能解析出模型清单时，**额外校验配置的模型确实存在**——这能覆盖最常见的
  "忘记 ollama pull"。模型名比对不做前缀匹配：`qwen3:1.7b` 与 `qwen3:4b` 是不同模型，
  放宽成前缀匹配会把"模型不存在"误判为正常。
- 结果带 TTL 缓存：健康检查通常每 30 秒一次，没必要每次都真打 Ollama。
- 不自动重试、不自动拉起服务，只如实报告——探测器的职责是暴露问题，不是掩盖它。
- 请求头与真实提问共用同一构造逻辑；缺少 API key 时直接判未就绪，避免发送非法的
  空 `Bearer` 请求头。
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
        self._probe_lock = threading.Lock()
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

        cached = self._cached_result(force)
        if cached is not None:
            return cached

        # /api/ready 无需登录。缓存尚未建立或刚过期时，多个并发请求可能同时到达；
        # 只允许一个请求真正访问模型服务，其余请求等待后复用刚写入的缓存。
        with self._probe_lock:
            cached = self._cached_result(force)
            if cached is not None:
                return cached
            result = self._do_probe()
            with self._lock:
                self._result = result
                self._checked_monotonic = time.monotonic()
            return result

    def _cached_result(self, force: bool) -> ProbeResult | None:
        with self._lock:
            if (
                force
                or self._result is None
                or time.monotonic() - self._checked_monotonic >= settings.llm_probe_ttl_s
            ):
                return None
            return ProbeResult(
                self._result.ok,
                self._result.message,
                self._result.checked_at,
                cached=True,
            )

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
                f"连接模型服务超时（{timeout:g} 秒）。"
                "请确认 Ollama 正在运行、地址可达且未被防火墙拦截。",
                now_iso(),
            )
        except httpx.LocalProtocolError:
            # 请求在建连之前就被拒了：本地配置非法，不是 Ollama 的问题。
            # 单独成一类，否则运维会去反复重启一个本来正常的 Ollama。
            return ProbeResult(
                False,
                "探测请求本身不符合 HTTP 协议，尚未发出。"
                "请检查 DEEPSEEK_BASE_URL 是否形如 http://主机:端口，"
                "以及 DEEPSEEK_API_KEY 是否含控制字符。",
                now_iso(),
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                False,
                f"无法连接模型服务（{exc.__class__.__name__}）。"
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
                f"模型服务返回网关错误 HTTP {response.status_code}。"
                "常见原因是请求被系统/环境代理接管（代理无法转发本机或内网地址），"
                "其次才是前置反向代理不可用。请检查 HTTP_PROXY/HTTPS_PROXY 与操作系统"
                "代理设置；本产品默认不使用代理（RAG_LLM_TRUST_ENV_PROXY=0）。",
                now_iso(),
            )

        if 300 <= response.status_code < 400:
            return ProbeResult(
                False,
                f"模型服务探测请求被重定向（HTTP {response.status_code}）。"
                "请检查 DEEPSEEK_BASE_URL 是否指向正确的 OpenAI 兼容接口。",
                now_iso(),
            )
        if response.status_code in (401, 403):
            return ProbeResult(
                False,
                f"模型服务拒绝鉴权（HTTP {response.status_code}）。"
                "请检查 DEEPSEEK_API_KEY 及该账号的模型访问权限。",
                now_iso(),
            )
        if response.status_code == 402:
            return ProbeResult(
                False,
                "模型服务返回 HTTP 402，账户余额或调用额度不足。",
                now_iso(),
            )
        if response.status_code == 429:
            return ProbeResult(
                False,
                "模型服务返回 HTTP 429，当前已被限流，请稍后重试。",
                now_iso(),
            )
        if response.status_code >= 500:
            return ProbeResult(
                False,
                f"模型服务返回 HTTP {response.status_code}，当前无法提供回答。",
                now_iso(),
            )
        if response.status_code >= 400 and response.status_code not in (404, 405):
            return ProbeResult(
                False,
                f"模型服务探测请求返回 HTTP {response.status_code}，请检查服务端配置。",
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
            return ProbeResult(True, "模型服务可达，已找到配置的模型", now_iso())

        return ProbeResult(
            False,
            "模型服务可达，但没有找到配置的模型。"
            "请运行 ollama list，并核对或导入 DEEPSEEK_MODEL 指定的精确模型标签。",
            now_iso(),
        )


llm_probe = LLMProbe()
