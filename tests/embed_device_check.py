"""嵌入设备配置（RAG_EMBED_DEVICE / RAG_EMBED_BATCH_SIZE）的行为测试。

设计要点：本测试**不需要 GPU、不需要 torch、不需要网络**。
CI 的 smoke 依赖里没有 torch，因此 device_available() 的 ImportError 分支
正好被这里完整覆盖；本机装了 torch 时同样能跑（断言写成环境无关）。

覆盖：
  1. 设备名的合法/非法写法（启动校验必须拒绝拼错的值）
  2. 默认值必须是 cpu（保证不改变现有 CPU 部署的行为）
  3. device_available() 对 cpu / 未知设备 / 空值的确定性行为
  4. batch size 边界
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.embeddings import EmbeddingService, device_available  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


# 一份可通过启动校验的最小合法配置
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
    try:
        settings_with(**overrides).validate_or_raise()
    except RuntimeError as exc:
        return str(exc)
    return ""


def accepted(**overrides) -> bool:
    return rejected(**overrides) == ""


def main() -> None:
    print("\n== 默认值必须保持 CPU（不改变现有部署行为） ==")
    check(settings_with().embed_device == "cpu", "未设置 RAG_EMBED_DEVICE 时默认为 cpu")
    check(settings_with().embed_batch_size == 32, "未设置 RAG_EMBED_BATCH_SIZE 时默认为 32")

    print("\n== 合法设备名必须放行 ==")
    for value in ("cpu", "mps", "cuda", "cuda:0", "cuda:3", "CUDA", " cuda:0 "):
        check(accepted(RAG_EMBED_DEVICE=value), f"接受合法设备名 {value!r}")

    print("\n== 非法设备名必须拒绝启动 ==")
    for value in ("gpu", "cuda:x", "cuda:-1", "tpu", "cuda:", "nvidia", "cuda:0:1"):
        reason = rejected(RAG_EMBED_DEVICE=value)
        check(
            "RAG_EMBED_DEVICE" in reason,
            f"拒绝非法设备名 {value!r}，且错误信息指出该变量",
        )
    check(
        "cuda:0" in rejected(RAG_EMBED_DEVICE="gpu"),
        "拒绝信息里列出允许的写法（含 cuda:0）",
    )

    print("\n== batch size 边界 ==")
    check(accepted(RAG_EMBED_BATCH_SIZE="1"), "接受 batch size = 1")
    check(accepted(RAG_EMBED_BATCH_SIZE="256"), "接受 batch size = 256")
    check("RAG_EMBED_BATCH_SIZE" in rejected(RAG_EMBED_BATCH_SIZE="0"), "拒绝 batch size = 0")
    check("RAG_EMBED_BATCH_SIZE" in rejected(RAG_EMBED_BATCH_SIZE="257"), "拒绝 batch size = 257")

    print("\n== mock 后端下设备设置不应阻止启动 ==")
    check(
        accepted(RAG_EMBED_BACKEND="mock", RAG_EMBED_DEVICE="cuda"),
        "mock 后端 + cuda 设备名仍可通过启动校验（mock 不真正加载模型）",
    )

    print("\n== device_available() 行为（环境无关断言） ==")
    check(device_available("cpu") == (True, ""), "cpu 恒可用")
    check(device_available("CPU") == (True, ""), "cpu 不区分大小写")

    ok, reason = device_available("")
    check(not ok and reason, "空设备名判定为不可用且给出原因")

    ok, reason = device_available("tpu")
    check(not ok and "tpu" in reason, "未知设备返回不可用并回显设备名")

    # cuda 的可用性取决于机器；只断言"要么可用、要么给出可操作的原因"，不写死结果
    ok_cuda, reason_cuda = device_available("cuda")
    check(isinstance(ok_cuda, bool), "cuda 检测返回布尔值而不抛异常")
    check(
        ok_cuda or bool(reason_cuda.strip()),
        "cuda 不可用时必须给出非空原因（不能默默失败）",
    )
    if not ok_cuda:
        print(f"        （本机 cuda 不可用原因：{reason_cuda}）")

    ok_mps, reason_mps = device_available("mps")
    check(
        ok_mps or bool(reason_mps.strip()),
        "mps 不可用时必须给出非空原因",
    )

    real_import = __import__

    def fail_torch_import(name, *args, **kwargs):
        if name == "torch":
            raise OSError("simulated missing DLL")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fail_torch_import):
        ok_broken, reason_broken = device_available("cuda")
    check(
        not ok_broken and "PyTorch 无法加载" in reason_broken,
        "PyTorch 因 DLL/运行库错误无法导入时返回可诊断结果，不让加载线程卡住",
    )

    with patch("app.embeddings.settings.embed_device", "cuda"), patch(
        "builtins.__import__", side_effect=fail_torch_import
    ):
        service = EmbeddingService()
        service._load_real()
    check(
        service.state == "error" and service.device == "cuda" and "PyTorch 无法加载" in service.message,
        "PyTorch 加载失败时嵌入服务进入 error 状态并保留请求设备",
    )

    # 越界序号不应崩溃
    ok_idx, reason_idx = device_available("cuda:99")
    check(isinstance(ok_idx, bool), "越界设备序号不会抛异常")

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        print("失败项:")
        for item in FAIL:
            print(f"  - {item}")
        sys.exit(1)


if __name__ == "__main__":
    main()
