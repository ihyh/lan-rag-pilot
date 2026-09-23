"""启动配置校验（fail-closed）的行为测试。

docs/SECURITY_AUDIT.md 第 2、3 条记录了两个真实缺口：
  - 模型地址缺失时会回退到公共 URL；
  - RAG_SECRET_KEY 缺失时只警告、不阻止启动。
本测试直接断言这两条已被关闭，并覆盖内网判定的正反例。

无需网络、模型或数据库。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


# 一份可用于启动的最小合法配置
GOOD = {
    "RAG_SECRET_KEY": "unit-test-secret-0123456789abcdef",
    "DEEPSEEK_API_KEY": "ollama",
    "DEEPSEEK_BASE_URL": "http://127.0.0.1:11434/v1",
}


def settings_with(**overrides) -> Settings:
    """在与外部环境隔离的前提下构造 Settings（不污染调用方环境）。"""
    saved = dict(os.environ)
    try:
        for key in list(os.environ):
            if key.startswith(("RAG_", "DEEPSEEK_", "HF_", "TRANSFORMERS_")):
                del os.environ[key]
        for key, value in {**GOOD, **overrides}.items():
            if value is not None:
                os.environ[key] = value
        return Settings()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def rejected(**overrides) -> str:
    """返回拒绝原因；未被拒绝时返回空串。"""
    try:
        settings_with(**overrides).validate_or_raise()
    except RuntimeError as exc:
        return str(exc)
    return ""


def accepted(**overrides) -> str:
    """返回空串表示通过；否则返回拒绝原因。"""
    return rejected(**overrides)


def main() -> None:
    print("\n== 合法配置必须放行 ==")
    check(accepted() == "", "完整合法配置可启动")
    check(accepted(RAG_PUBLIC_ORIGIN=None) == "", "未配置 RAG_PUBLIC_ORIGIN 可启动")
    check(
        accepted(DEEPSEEK_BASE_URL="http://192.168.136.128:11434/v1") == "",
        "私有网段 IP（192.168/16）作为模型地址可启动",
    )
    check(
        accepted(DEEPSEEK_BASE_URL="http://10.1.2.3:11434/v1") == "",
        "私有网段 IP（10/8）作为模型地址可启动",
    )
    check(
        accepted(DEEPSEEK_BASE_URL="http://ollama:11434/v1") == "",
        "单标签主机名（Docker 服务名 ollama）可启动",
    )
    check(
        accepted(DEEPSEEK_BASE_URL="http://rag-llm.internal:11434/v1") == "",
        ".internal 内网域名可启动",
    )
    check(
        accepted(
            DEEPSEEK_BASE_URL="https://llm.corp.example.com/v1",
            RAG_LLM_TRUSTED_HOSTS="llm.corp.example.com",
        )
        == "",
        "显式加入 RAG_LLM_TRUSTED_HOSTS 的主机可启动",
    )
    check(
        accepted(RAG_PUBLIC_ORIGIN="http://127.0.0.1:8088") == "",
        "HTTP + 回环来源可启动",
    )
    check(
        accepted(RAG_PUBLIC_ORIGIN="https://rag.corp.internal", RAG_COOKIE_SECURE="true") == "",
        "HTTPS + Cookie Secure 可启动",
    )

    print("\n== 不安全或缺配置必须拒绝启动 ==")
    reason = rejected(RAG_SECRET_KEY=None)
    check("RAG_SECRET_KEY" in reason, "缺少 RAG_SECRET_KEY 时拒绝启动")
    check("secrets.token_urlsafe" in reason, "拒绝信息包含可直接复制的密钥生成命令")

    check("DEEPSEEK_API_KEY" in rejected(DEEPSEEK_API_KEY=None), "缺少 DEEPSEEK_API_KEY 时拒绝启动")
    # 只有空白也必须拒绝：它既能通过这里的裸属性判断，也能通过 chat 里
    # `if not settings.deepseek_api_key` 的判断，最后表现为发出非法的
    # `Authorization: Bearer `（带尾随空格），被报成"无法连接模型服务"的伪网络故障。
    check(
        "DEEPSEEK_API_KEY" in rejected(DEEPSEEK_API_KEY="   "),
        "只有空白的 DEEPSEEK_API_KEY 也在启动时被拒绝（而非拖到用户提问才失败）",
    )
    check(
        "DEEPSEEK_API_KEY" in rejected(DEEPSEEK_API_KEY=""),
        "显式空字符串的 DEEPSEEK_API_KEY 被拒绝",
    )
    check(
        "控制字符" in rejected(DEEPSEEK_API_KEY="secret\nvalue"),
        "含换行等控制字符的 DEEPSEEK_API_KEY 在启动时被拒绝",
    )
    check(
        accepted(DEEPSEEK_API_KEY="ollama") == "",
        "非空占位值 ollama 可启动（本地 Ollama 的正常用法）",
    )
    check(
        "RAG_READY_PROBE_TTL_S" in rejected(RAG_READY_PROBE_TTL_S="nan"),
        "探针 TTL 不接受 NaN",
    )
    check(
        "RAG_READY_PROBE_TIMEOUT_S" in rejected(RAG_READY_PROBE_TIMEOUT_S="inf"),
        "探针超时不接受无穷大",
    )

    # 这一条直接对应“默认值会静默连公网”的历史缺口
    check(
        "非内网地址" in rejected(DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"),
        "公网模型地址（api.deepseek.com）被拒绝",
    )
    check(
        "非内网地址" in rejected(DEEPSEEK_BASE_URL="https://api.deepseek.com"),
        "config.py 的历史默认值本身已不能启动",
    )
    check(
        "RAG_LLM_TRUSTED_HOSTS" in rejected(DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"),
        "拒绝信息提示可用 RAG_LLM_TRUSTED_HOSTS 显式声明内网主机",
    )

    check(
        "RAG_COOKIE_SECURE" in rejected(
            RAG_PUBLIC_ORIGIN="https://rag.corp.internal", RAG_COOKIE_SECURE="false"
        ),
        "HTTPS 来源 + Cookie 非 Secure 被拒绝",
    )
    check(
        "登录会陷入循环" in rejected(
            RAG_PUBLIC_ORIGIN="http://rag.corp.internal", RAG_COOKIE_SECURE="true"
        ),
        "HTTP 非回环来源 + Cookie Secure 被拒绝",
    )

    print("\n== 逃生开关只降级为警告 ==")
    check(
        accepted(RAG_SECRET_KEY=None, RAG_ALLOW_INSECURE_START="1") == "",
        "RAG_ALLOW_INSECURE_START=1 时不阻止启动（仅警告）",
    )
    check(
        accepted(DEEPSEEK_BASE_URL="https://api.deepseek.com/v1", RAG_ALLOW_INSECURE_START="1") == "",
        "逃生开关同时放行公网地址",
    )
    check(
        rejected(RAG_SECRET_KEY=None, RAG_ALLOW_INSECURE_START="0") != "",
        "RAG_ALLOW_INSECURE_START=0 时仍然拒绝启动",
    )

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
