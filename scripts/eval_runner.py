"""运行真实 RAG 评测集：只调用现有登录和问答 API。"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cases(path: Path, min_cases: int = 30) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"评测文件不存在: {path}")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_no} 行不是合法 JSON: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"第 {line_no} 行必须是 JSON 对象")
        required = {"id", "question", "expected_answer", "expected_sources", "should_refuse"}
        missing = sorted(required - item.keys())
        if missing:
            raise ValueError(f"第 {line_no} 行缺少字段: {', '.join(missing)}")
        case_id = str(item["id"]).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"第 {line_no} 行 id 为空或重复: {case_id!r}")
        question = str(item["question"]).strip()
        answer = str(item["expected_answer"]).strip()
        if not question or not answer:
            raise ValueError(f"第 {line_no} 行 question/expected_answer 不能为空")
        if any(marker in (question + answer) for marker in ("TODO", "REPLACE_ME", "待填写")):
            raise ValueError(f"第 {line_no} 行仍含模板占位符")
        sources = item["expected_sources"]
        if not isinstance(sources, list):
            raise ValueError(f"第 {line_no} 行 expected_sources 必须是数组")
        for source in sources:
            if not isinstance(source, dict) or not str(source.get("filename", "")).strip():
                raise ValueError(f"第 {line_no} 行来源必须包含 filename")
            if not any(source.get(k) is not None for k in ("page", "paragraph", "chunk_id")):
                raise ValueError(f"第 {line_no} 行来源至少要有 page、paragraph 或 chunk_id")
        if not isinstance(item["should_refuse"], bool):
            raise ValueError(f"第 {line_no} 行 should_refuse 必须是布尔值")
        keywords = item.get("answer_keywords", [])
        if not isinstance(keywords, list) or not all(str(x).strip() for x in keywords):
            raise ValueError(f"第 {line_no} 行 answer_keywords 必须是非空字符串数组")
        seen.add(case_id)
        cases.append(item)
    if len(cases) < min_cases:
        raise ValueError(f"评测样本只有 {len(cases)} 条，当前要求至少 {min_cases} 条真实样本")
    return cases


def _same_location(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if str(expected.get("filename", "")).casefold() != str(actual.get("filename", "")).casefold():
        return False
    for key in ("page", "paragraph", "chunk_id"):
        value = expected.get(key)
        if value is not None and str(value) != str(actual.get(key)):
            return False
    return True


def _same_document(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return str(expected.get("filename", "")).casefold() == str(actual.get("filename", "")).casefold()


def evaluate_case(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    answer = str(response.get("answer") or "")
    actual_sources = response.get("sources") or []
    expected_sources = case["expected_sources"]
    refusal_observed = len(actual_sources) == 0
    expected_refusal = bool(case["should_refuse"])
    retrieval_hit = (
        refusal_observed if expected_refusal else any(
            _same_document(expected, actual)
            for expected in expected_sources
            for actual in actual_sources
        )
    )
    citation_location_hit = (
        refusal_observed if expected_refusal else any(
            _same_location(expected, actual)
            for expected in expected_sources
            for actual in actual_sources
        )
    )
    keywords = [str(x).casefold() for x in case.get("answer_keywords", [])]
    answer_keywords_hit = all(word in answer.casefold() for word in keywords)
    answer_check = "pass" if keywords and answer_keywords_hit else ("manual" if not keywords else "fail")
    return {
        "id": case["id"],
        "question": case["question"],
        "expected_answer": case["expected_answer"],
        "expected_sources": expected_sources,
        "answer": answer,
        "sources": actual_sources,
        "expected_refusal": expected_refusal,
        "refusal_observed": refusal_observed,
        "refusal_match": expected_refusal == refusal_observed,
        "retrieval_hit": retrieval_hit,
        "citation_location_hit": citation_location_hit,
        "citation_hit": citation_location_hit,
        "answer_keywords_hit": answer_keywords_hit,
        "answer_check": answer_check,
        "auto_pass": bool(answer) and (expected_refusal == refusal_observed) and citation_location_hit and answer_check != "fail",
    }


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
            except (ValueError, UnicodeDecodeError):
                detail = ""
            raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"无法连接评测服务: {exc.reason}") from exc


def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(Path(args.cases), args.min_cases)
    password = os.environ.get(args.password_env) if args.password_env else None
    if not password:
        password = getpass.getpass("RAG 评测账号密码（不会写入报告）: ")
    client = ApiClient(args.base_url, args.timeout)
    client.request("/api/login", "POST", {"username": args.username, "password": password})
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        started = time.monotonic()
        try:
            result = client.request("/api/query", "POST", {"question": case["question"]})
            result = evaluate_case(case, result)
            result["error"] = None
        except RuntimeError as exc:
            result = {"id": case["id"], "question": case["question"], "auto_pass": False, "error": str(exc)}
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        results.append(result)
        print(f"[{index}/{len(cases)}] {case['id']}: {'PASS' if result['auto_pass'] else 'CHECK'}")
    summary = {
        "cases": len(results),
        "query_errors": sum(1 for x in results if x.get("error")),
        "retrieval_or_refusal_pass": sum(1 for x in results if x.get("citation_hit")),
        "refusal_match": sum(1 for x in results if x.get("refusal_match")),
        "auto_pass": sum(1 for x in results if x.get("auto_pass")),
        "answer_manual_review": sum(1 for x in results if x.get("answer_check") == "manual"),
    }
    total = max(1, summary["cases"])
    answerable = [x for x in results if not x.get("expected_refusal")]
    answerable_total = max(1, len(answerable))
    summary.update(
        {
            "retrieval_or_refusal_rate": round(summary["retrieval_or_refusal_pass"] / total, 4),
            "refusal_accuracy": round(summary["refusal_match"] / total, 4),
            "auto_pass_rate": round(summary["auto_pass"] / total, 4),
            "answerable_cases": len(answerable),
            "top5_hit_rate": round(sum(1 for x in answerable if x.get("retrieval_hit")) / answerable_total, 4),
            "citation_location_accuracy": round(
                sum(1 for x in answerable if x.get("citation_location_hit")) / answerable_total, 4
            ),
        }
    )
    return {"generated_at": _now(), "base_url": args.base_url.rstrip("/"), "summary": summary, "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 RAG 真实问题评测")
    parser.add_argument("--cases", default="data/eval/questions.jsonl")
    parser.add_argument("--out", default="data/eval/reports/latest.json")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--username", default="eval_user")
    parser.add_argument("--password-env", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--min-cases", type=int, default=30)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        cases = load_cases(Path(args.cases), args.min_cases)
        print(f"评测集校验通过：{len(cases)} 条")
        if args.validate_only:
            return 0
        report = run(args)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入: {out}")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"评测失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
