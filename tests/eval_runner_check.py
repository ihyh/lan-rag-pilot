"""评测格式和单条结果计算的最小自检。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.eval_runner as eval_runner

evaluate_case = eval_runner.evaluate_case
load_cases = eval_runner.load_cases


def main() -> None:
    case = {
        "id": "q001",
        "question": "系统支持哪些格式？",
        "expected_answer": "支持 PDF。",
        "expected_sources": [{"filename": "guide.pdf", "page": 2}],
        "answer_keywords": ["PDF"],
        "should_refuse": False,
    }
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "cases.jsonl"
        path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
        assert len(load_cases(path, min_cases=1)) == 1
        try:
            load_cases(path, min_cases=30)
        except ValueError as exc:
            assert "至少 30 条" in str(exc)
        else:
            raise AssertionError("少于最小样本数时必须失败")
        inconsistent = [
            ({**case, "expected_sources": []}, "非拒答样本必须包含 expected_sources"),
            ({**case, "should_refuse": True}, "拒答样本的 expected_sources 必须为空"),
        ]
        for broken, message in inconsistent:
            path.write_text(json.dumps(broken, ensure_ascii=False) + "\n", encoding="utf-8")
            try:
                load_cases(path, min_cases=1)
            except ValueError as exc:
                assert message in str(exc)
            else:
                raise AssertionError(message)
        path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

        class UnexpectedClient:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("非法 min_interval 必须在创建客户端前被拒绝")

        original_client = eval_runner.ApiClient
        eval_runner.ApiClient = UnexpectedClient
        os.environ["RAG_EVAL_TEST_PASSWORD"] = "not-a-real-secret"
        try:
            for invalid in (-0.1, float("nan"), float("inf"), float("-inf")):
                try:
                    eval_runner.run(argparse.Namespace(
                        cases=str(path), min_cases=1, password_env="RAG_EVAL_TEST_PASSWORD",
                        base_url="http://127.0.0.1:1", timeout=1.0, username="eval_user",
                        min_interval=invalid,
                    ))
                except ValueError as exc:
                    assert "min_interval" in str(exc)
                else:
                    raise AssertionError("min_interval 必须是有限且不小于 0 的数")
        finally:
            os.environ.pop("RAG_EVAL_TEST_PASSWORD", None)
            eval_runner.ApiClient = original_client
    result = evaluate_case(case, {"answer": "支持 PDF。", "sources": [{"filename": "guide.pdf", "page": 2}]})
    assert result["auto_pass"] is True
    wrong_page = evaluate_case(case, {"answer": "支持 PDF。", "sources": [{"filename": "guide.pdf", "page": 3}]})
    assert wrong_page["retrieval_hit"] is True
    assert wrong_page["citation_location_hit"] is False
    manual_case = {key: value for key, value in case.items() if key != "answer_keywords"}
    manual = evaluate_case(
        manual_case, {"answer": "支持 PDF。", "sources": [{"filename": "guide.pdf", "page": 2}]}
    )
    assert manual["answer_check"] == "manual" and manual["auto_pass"] is False, \
        "需要人工复核的答案不得计入 auto_pass"
    spaced = evaluate_case(
        {**case, "expected_sources": [{"filename": " guide.pdf ", "page": 2}]},
        {"answer": "支持 PDF。", "sources": [{"filename": "GUIDE.PDF", "page": 2}]},
    )
    assert spaced["citation_location_hit"] is True, "文件名比较应忽略首尾空白和大小写"

    refusal = {
        **manual_case,
        "id": "q002",
        "question": "资料中没有的问题？",
        "expected_answer": "应拒答。",
        "expected_sources": [],
        "should_refuse": True,
    }
    refusal_result = evaluate_case(
        refusal, {"answer": "知识库没有相关依据。", "sources": []}
    )
    assert refusal_result["refusal_match"] is True
    assert refusal_result["answer_check"] == "manual" and refusal_result["auto_pass"] is False, \
        "拒答判断命中不等于回答文本已自动验收"
    error_refusal = {**refusal, "id": "q003", "question": "另一个无答案问题？"}
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "cases.jsonl"
        path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False)
                      for item in (case, refusal, error_refusal)) + "\n",
            encoding="utf-8",
        )
        responses = iter([
            {"answer": "支持 PDF。", "sources": [{"filename": "guide.pdf", "page": 2}]},
            {"answer": "知识库没有相关依据。", "sources": []},
            RuntimeError("模拟查询失败"),
        ])

        class FakeApiClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def request(self, route, _method="GET", _payload=None):
                if route == "/api/login":
                    return {}
                response = next(responses)
                if isinstance(response, Exception):
                    raise response
                return response

        clock = [0.0]
        sleeps = []
        original_client = eval_runner.ApiClient
        original_monotonic = eval_runner.time.monotonic
        original_sleep = eval_runner.time.sleep
        eval_runner.ApiClient = FakeApiClient
        eval_runner.time.monotonic = lambda: clock[0]
        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        eval_runner.time.sleep = fake_sleep
        os.environ["RAG_EVAL_TEST_PASSWORD"] = "not-a-real-secret"
        try:
            report = eval_runner.run(argparse.Namespace(
                cases=str(path), min_cases=3, password_env="RAG_EVAL_TEST_PASSWORD",
                base_url="http://127.0.0.1:1", timeout=1.0, username="eval_user",
                min_interval=6.1,
            ))
        finally:
            os.environ.pop("RAG_EVAL_TEST_PASSWORD", None)
            eval_runner.ApiClient = original_client
            eval_runner.time.monotonic = original_monotonic
            eval_runner.time.sleep = original_sleep
        assert sleeps == [6.1, 6.1], f"连续评测请求应按最小间隔节流：{sleeps}"
        assert "document_hit_rate" in report["summary"], \
            "摘要必须使用不假定 Top-K 的 document_hit_rate"
        assert report["summary"]["answerable_cases"] == 1, \
            "拒答题即使查询失败也不得混入可回答题分母"
        assert report["summary"]["document_hit_rate"] == 1.0
        assert report["summary"]["observed_max_sources"] == 1
        assert report["summary"]["query_errors"] == 1
        assert "top5_hit_rate" not in report["summary"], "未知运行时 Top-K 时不得声称 Top-5"
    print("eval runner check ok")


if __name__ == "__main__":
    main()
