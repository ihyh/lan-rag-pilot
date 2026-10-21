"""表格参数题确定性回答检查：行号/列义/单位不被模型读错，直接由单元格拼装。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

TMP = Path(tempfile.mkdtemp(prefix="rag-table-"))
os.environ.setdefault("RAG_DATA_DIR", str(TMP))
os.environ.setdefault("RAG_DB_PATH", str(TMP / "table.db"))
os.environ.setdefault("RAG_EMBED_BACKEND", "mock")
os.environ.setdefault("RAG_SECRET_KEY", "table-check-secret-0123456789")
os.environ.setdefault("RAG_ROOT_PASSWORD", "table-check-root-pw")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import llm as llm_module  # noqa: E402
from app.table_answers import build_table_answer  # noqa: E402

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


def header_source() -> dict:
    return {
        "filename": "PLM_parameters.xlsx", "page": None, "paragraph": 1,
        "chunk_id": 1, "score": 0.5,
        "content": (
            "工作表《Parameter》第 1 行：A1=Index；B1=Name；C1=Unit；D1=Description；E1=Mark\n"
            "工作表《Parameter》第 2 行：A2=0；B2=Input Monitor1；C2=/；D2=监控的输入通道号；"
            "E2=通道输入信号变化时自动上报"
        ),
    }


def row_source() -> dict:
    return {
        "filename": "PLM_parameters.xlsx", "page": None, "paragraph": 2,
        "chunk_id": 2, "score": 0.5,
        "content": (
            "工作表《Parameter》第 18 行：A18=20；B18=Input Monitor Time；C18=/；"
            "D18=监控输入的时间间隔；E18=扫描输入变化的时间间隔,单位ms"
        ),
    }


def test_index_unit_description() -> None:
    print("\n== 索引/字段说明/单位：不把行号当索引 ==")
    sources = [header_source(), row_source()]
    result = build_table_answer(
        "在PLM_parameters.xlsx中，Input Monitor Time的索引、字段说明及时间单位是什么？"
        "表头Unit列与Mark列的单位文字请区分。", sources,
    )
    check(result is not None, "表格题命中确定性回答")
    answer = result["answer"] if result else ""
    check(result and result["model"] == "table_answers", "标记为 table_answers，不经过模型")
    check("第 18 行" in answer, "回答带真实行号")
    check("索引（A列 Index）：20" in answer, "索引取 A18=20，不是行号 18")
    check("行号 18 不是索引值" in answer, "明确行号不等于索引")
    check("监控输入的时间间隔" in answer, "字段说明取 D18")
    check("Unit 列（C列）=/" in answer, "Unit 列取 C18=/")
    check("Mark 列（E列）=扫描输入变化的时间间隔,单位ms" in answer, "Mark 列取 E18 的单位说明")
    check("[2]" in answer, "引用目标行来源编号")


def test_current_value_unknown() -> None:
    print("\n== 现场当前值：只有定义时明确无法确认 ==")
    sources = [header_source(), row_source()]
    result = build_table_answer(
        "根据PLM_parameters.xlsx，P20的现场当前值是多少？请区分资料中的字段说明与已读取的实际现场值。",
        sources,
    )
    check(result is not None, "P20 按 Index=20 定位到字段")
    answer = result["answer"] if result else ""
    check("Input Monitor Time" in answer, "说明 P20 对应字段 Input Monitor Time")
    check("无法确认现场实际设置值" in answer, "只有定义时明确无法确认现场值")
    check("现场实际设置值：20" not in answer and "当前值：20" not in answer,
          "不把定义值/索引值冒充现场当前值")


def test_fallbacks() -> None:
    print("\n== 不命中时交回原生成路径 ==")
    sources = [header_source(), row_source()]
    check(build_table_answer("Input Monitor Time 是什么？", sources) is None,
          "未问索引/说明/单位/现场值的普通问题不接管")
    check(build_table_answer("这个表格怎么用？", sources) is None, "无属性关键词不接管")
    check(build_table_answer("Unknown Field 的索引是什么？", sources) is None,
          "字段未命中不接管")
    # “参数说明”是文档名的一部分，不是“字段说明”属性词，不能误触发。
    check(build_table_answer(
        "PLM_parameters.xlsx 的 Event Switch 参数150 与 PLM200W参数说明的 P150，"
        "bit4 分别记录什么？能否直接按同一位定义互换配置？",
        sources,
    ) is None, "“参数说明/记录什么”不当作字段说明，交回模型")
    pdf = dict(row_source(), filename="manual.pdf")
    check(build_table_answer("Input Monitor Time 的索引是什么？", [pdf]) is None,
          "非 xlsx 来源不接管")


def test_multi_source_definitions() -> None:
    print("\n== 同名字段多来源：分别列出，不裁定 ==")
    other = dict(row_source(),
                 filename="PLM200W_parameters.xlsx",
                 chunk_id=3, content=(
                     "工作表《Parameter》第 18 行：A18=20；B18=Input Monitor Time；"
                     "D18=另一份资料里的监控时间间隔；E18=单位ms"))
    result = build_table_answer("Input Monitor Time 的字段说明和单位是什么？",
                                [header_source(), row_source(), other])
    answer = result["answer"] if result else ""
    check(answer.count("第 18 行") == 2, "两份来源里的同名字段都列出")
    check("[2]" in answer and "[3]" in answer, "分别标注真实来源编号")


def test_llm_integration() -> None:
    print("\n== 开关开启时 llm.chat 走确定性表格回答 ==")
    sources = [header_source(), row_source()]
    with patch.object(llm_module, "_source_projection", return_value=None), \
         patch.object(llm_module.settings, "table_answers", True), \
         patch.object(llm_module, "model_service_client",
                      side_effect=AssertionError("不应调用模型服务")):
        result = llm_module.chat(
            "Input Monitor Time 的索引、字段说明和单位是什么？", sources
        )
    check(result["model"] == "table_answers", "未调用模型，直接返回确定性回答")
    check("索引（A列 Index）：20" in result["answer"], "集成路径也按单元格拼装")


def main() -> int:
    test_index_unit_description()
    test_current_value_unknown()
    test_fallbacks()
    test_multi_source_definitions()
    test_llm_integration()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
