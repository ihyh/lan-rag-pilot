"""Byte 参数块确定性回答检查：单位/限制/默认值按原文逐项拼装。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

TMP = Path(tempfile.mkdtemp(prefix="rag-param-"))
os.environ.setdefault("RAG_DATA_DIR", str(TMP))
os.environ.setdefault("RAG_DB_PATH", str(TMP / "param.db"))
os.environ.setdefault("RAG_EMBED_BACKEND", "mock")
os.environ.setdefault("RAG_SECRET_KEY", "param-check-secret-0123456789")
os.environ.setdefault("RAG_ROOT_PASSWORD", "param-check-root-pw")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import llm as llm_module  # noqa: E402
from app.parameter_answers import build_parameter_answer  # noqa: E402

PASS = 0
FAIL = 0


def check(condition: bool, message: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {message}")
    else:
        FAIL += 1
        print(f"  [FAIL] {message}")


def sources() -> list[dict]:
    return [
        {
            "filename": "rfid.pdf", "page": 6, "paragraph": 1, "chunk_id": 1, "score": 0.5,
            "content": (
                "Configure RFT-200S RFID READER: ID 0xA5\n"
                "Parameters: Byte 1: Inter-Character time-out in tenths of seconds "
                "(must be >4: if not, reverts to default\n"
                " value of 40)."
            ),
        },
        {
            "filename": "rfid.pdf", "page": 7, "paragraph": 2, "chunk_id": 2, "score": 0.5,
            "content": (
                " Byte 2: ENQ-EOT time-out in tenths of seconds (must be >19: if not, reverts to default\n"
                " value of 200).\n"
                " Byte 3: Message retry count (must be >1: if no, reverts to default value of 2)."
            ),
        },
    ]


def test_bytes() -> None:
    print("\n== Byte1/2/3：控制/限制/默认/单位按原文 ==")
    result = build_parameter_answer(
        "RFT-200S配置命令0xA5的Byte1、Byte2和Byte3分别控制什么？"
        "各自限制、默认值和计量单位有哪些明确记录？",
        sources(),
    )
    check(result is not None, "Byte 参数题命中确定性回答")
    answer = result["answer"] if result else ""
    check(result and result["model"] == "parameter_answers", "标记为 parameter_answers，不经过模型")
    check("Byte 1：控制 Inter-Character time-out" in answer, "Byte1 控制内容")
    check("限制必须大于 4" in answer and "默认 40" in answer, "Byte1 限制/默认值")
    check("单位 tenths of seconds" in answer, "Byte1 单位保留原文写法")
    check("Byte 2：控制 ENQ-EOT time-out" in answer, "Byte2 控制内容")
    check("限制必须大于 19" in answer and "默认 200" in answer, "Byte2 限制/默认值")
    check("Byte 3：控制 Message retry count" in answer, "Byte3 控制内容")
    check("限制必须大于 1" in answer and "默认 2" in answer, "Byte3 限制/默认值")
    check("Byte 3" in answer and "未给出计量单位" in answer, "Byte3 不补时间单位")
    check("tenths of seconds" not in answer.split("Byte 3")[1], "Byte3 不继承前两项的时间单位")
    check("[1]" in answer and "[2]" in answer, "分别标注真实来源编号")


def test_fallbacks() -> None:
    print("\n== 不命中时交回原生成路径 ==")
    check(build_parameter_answer("这个命令怎么用？", sources()) is None, "未点名 Byte 不接管")
    check(build_parameter_answer("Byte 9 是什么？", sources()) is None, "片段没有的 Byte 不接管")
    check(build_parameter_answer("Byte1 和 Byte2 分别是什么？", sources()) is None,
          "没有控制/限制/默认/单位属性词不接管")
    check(build_parameter_answer("Byte1 的限制是什么？", [{"filename": "x.pdf", "content": "无参数"}]) is None,
          "没有 Byte 块不接管")


def test_llm_integration() -> None:
    print("\n== 开关开启时 llm.chat 走 Byte 参数确定性回答 ==")
    with patch.object(llm_module, "_source_projection", return_value=None), \
         patch.object(llm_module.settings, "table_answers", False), \
         patch.object(llm_module.settings, "parameter_answers", True), \
         patch.object(llm_module, "model_service_client",
                      side_effect=AssertionError("不应调用模型服务")):
        result = llm_module.chat("Byte1、Byte2、Byte3 分别控制什么？限制、默认值和单位？", sources())
    check(result["model"] == "parameter_answers", "未调用模型，直接返回确定性回答")
    check("Byte 3：控制 Message retry count" in result["answer"], "集成路径同样逐项拼装")


def main() -> int:
    test_bytes()
    test_fallbacks()
    test_llm_integration()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
