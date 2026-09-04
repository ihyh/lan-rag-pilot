"""评测格式和单条结果计算的最小自检。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_runner import evaluate_case, load_cases


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
    result = evaluate_case(case, {"answer": "支持 PDF。", "sources": [{"filename": "guide.pdf", "page": 2}]})
    assert result["auto_pass"] is True
    wrong_page = evaluate_case(case, {"answer": "支持 PDF。", "sources": [{"filename": "guide.pdf", "page": 3}]})
    assert wrong_page["retrieval_hit"] is True
    assert wrong_page["citation_location_hit"] is False
    print("eval runner check ok")


if __name__ == "__main__":
    main()
