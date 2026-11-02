"""Validate human-approved standalone cases; execute only against a declared isolated service."""
from __future__ import annotations

import argparse
import getpass
from http.cookiejar import CookieJar
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, build_opener

try:
    from .eval_runner import ApiClient, _now, evaluate_case
except ImportError:
    from eval_runner import ApiClient, _now, evaluate_case

MAX_ID = 2**63 - 1
MANIFEST_FIELDS = ("document_id", "filename", "sha256", "version", "index_sha256")


def _id(value):
    return type(value) is int and 0 < value <= MAX_ID


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _ids(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 100
            or not all(_id(item) for item in value) or len(set(value)) != len(value)):
        raise ValueError("document_ids 必须为 1–100 个不重复的 SQLite 正整数，禁止空范围")
    return sorted(value)


def _manifest(items, document_ids):
    if not isinstance(items, list) or len(items) != len(document_ids):
        raise ValueError("scope_documents/manifest 必须与 document_ids 一一对应")
    result = []
    for item in items:
        if (not isinstance(item, dict) or any(key not in item for key in MANIFEST_FIELDS)
                or not _id(item["document_id"]) or not _text(item["filename"])
                or not _sha(item["sha256"]) or not _sha(item["index_sha256"])
                or (item["version"] is not None and not isinstance(item["version"], str))):
            raise ValueError("文档 manifest 的 ID/文件名/SHA/版本字段不合法")
        result.append({key: item[key] for key in MANIFEST_FIELDS})
    if sorted(item["document_id"] for item in result) != document_ids:
        raise ValueError("scope_documents/manifest 的文档 ID 不一致或重复")
    return sorted(result, key=lambda item: item["document_id"])


def load_cases(path: Path):
    cases, seen = [], set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
            if not isinstance(item, dict) or item.get("format") != "rag-eval-v2":
                raise ValueError("仅接受 rag-eval-v2，不接受反馈候选或旧格式")
            if (item.get("human_verified") is not True or item.get("runnable_eval_case") is not True
                    or item.get("requires_source_revalidation") is not False):
                raise ValueError("需要明确人工确认、可执行、来源已复核三个标记")
            if item.get("context_mode") != "standalone" or item.get("scope_mode") != "selected":
                raise ValueError("仅接受人工完整独立问题和明确 selected 文档范围")
            if not _text(item.get("id")) or item["id"] in seen:
                raise ValueError("id 必须非空且不重复")
            if (not _text(item.get("question")) or len(item["question"].strip()) > 2000
                    or not _text(item.get("expected_answer"))):
                raise ValueError("question/expected_answer 必须非空，question 不超过 2000 字符")
            if any(marker in item["question"] + item["expected_answer"]
                   for marker in ("TODO", "REPLACE_ME", "待填写")):
                raise ValueError("不能执行含模板占位符的问题")
            item["document_ids"] = _ids(item.get("document_ids"))
            item["scope_documents"] = _manifest(item.get("scope_documents"), item["document_ids"])
            documents = {doc["document_id"]: doc for doc in item["scope_documents"]}
            sources = item.get("expected_sources")
            if type(item.get("should_refuse")) is not bool or not isinstance(sources, list):
                raise ValueError("should_refuse 必须为布尔值，expected_sources 必须为数组")
            if bool(sources) == item["should_refuse"]:
                raise ValueError("非拒答题必须有正确来源，拒答题来源必须为空")
            chunks = set()
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError("正确来源必须是对象")
                doc = documents.get(source.get("document_id")) if _id(source.get("document_id")) else None
                if (doc is None or not _id(source.get("chunk_id"))
                        or source.get("filename") != doc["filename"] or source.get("sha256") != doc["sha256"]
                        or not _sha(source.get("chunk_content_sha256")) or not _text(source.get("excerpt"))):
                    raise ValueError("正确来源必须包含范围内 document_id/chunk_id、文件名、双 SHA 和人工摘录")
                if source.get("location_kind") == "image_caption":
                    if (Path(source["filename"]).suffix.lower() not in ('.png','.jpg','.jpeg')
                            or any(source.get(key) is not None for key in ("page", "paragraph", "line_start", "line_end"))):
                        raise ValueError("图片说明来源须为 PNG/JPEG，且不能混用页码、段落或行号")
                elif source.get("location_kind") is not None:
                    raise ValueError("未知来源定位类型")
                elif any(source.get(key) is not None for key in ("line_start", "line_end")):
                    if (not _id(source.get("line_start")) or not _id(source.get("line_end"))
                            or source["line_start"] > source["line_end"]
                            or any(source.get(key) is not None for key in ("page", "paragraph"))):
                        raise ValueError("日志来源必须包含完整有效的 line_start/line_end，且不混用页码或段落")
                elif (not any(_id(source.get(key)) for key in ("page", "paragraph"))
                        or any(source.get(key) is not None and not _id(source[key]) for key in ("page", "paragraph"))):
                    raise ValueError("正确来源必须包含有效正整数 page 或 paragraph")
                if source["chunk_id"] in chunks:
                    raise ValueError("每个 chunk 仅允许一条人工连续摘录")
                chunks.add(source["chunk_id"])
            keywords = item.get("answer_keywords", [])
            if not isinstance(keywords, list) or not all(_text(word) for word in keywords):
                raise ValueError("answer_keywords 必须为非空字符串数组")
            if "original" in item and not isinstance(item["original"], dict):
                raise ValueError("original 仅允许保存元数据对象，不作为请求上下文")
            if any(key in item for key in ("conversation_id", "history", "prior_turns")):
                raise ValueError("原上下文只能保存在 original 元数据中，不支持精确历史重放")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"第 {number} 行: {exc}") from exc
        seen.add(item["id"])
        cases.append(item)
    if not cases:
        raise ValueError("执行集不能为空")
    return cases


def isolated_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        local = host == "localhost" or bool(host and ipaddress.ip_address(host).is_loopback)
        if (parsed.scheme not in ("http", "https") or not local or parsed.port in (None, 8088)
                or not 0 < parsed.port <= 65535 or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or "?" in value or "#" in value
                or any(char.isspace() or ord(char) < 32 for char in value)):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("仅允许显式 loopback 非 8088 端口 URL，不含路径、凭据、query 或 fragment") from exc
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IsolatedClient(ApiClient):
    def __init__(self, base_url, timeout):
        super().__init__(isolated_url(base_url), timeout)
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())


def _check_manifest(client, case):
    response = client.request("/api/admin/eval-manifest", "POST", {"document_ids": case["document_ids"]})
    actual = _manifest(response.get("items"), case["document_ids"])
    if actual != case["scope_documents"]:
        raise RuntimeError("文档文件/版本/索引 manifest 已变化，结果不得作为同条件复测")
    images = [source for source in case['expected_sources'] if source.get('location_kind') == 'image_caption']
    for document_id in {source['document_id'] for source in images}:
        inspection = client.request(f'/api/library/documents/{document_id}/inspection?limit=50&offset=0', 'GET')
        document = inspection.get('document') or {}
        try:
            tags = json.loads(document.get('tags') or '[]')
        except (TypeError, ValueError) as exc:
            raise RuntimeError('图片资料标签无效，不能复测') from exc
        if (document.get('id') != document_id or not isinstance(tags,list)
                or not {'image_caption_only','image_text_direct'}.issubset(tags)):
            raise RuntimeError('图片资料已不再是直接入库的人工说明，不能复测')
        chunks = {item.get('chunk_id'):item for item in inspection.get('items',[]) if isinstance(item,dict)}
        for expected in (item for item in images if item['document_id'] == document_id):
            actual_chunk = chunks.get(expected['chunk_id']) or {}
            if (actual_chunk.get('document_id') != document_id or actual_chunk.get('sha256') != expected['sha256']
                    or actual_chunk.get('chunk_content_sha256') != expected['chunk_content_sha256']
                    or expected['excerpt'] not in (actual_chunk.get('content') or '')):
                raise RuntimeError('图片说明片段或原件身份已变化，不能复测')
    return actual


def run(args):
    cases = load_cases(Path(args.cases))
    if not args.execute or not args.isolated:
        raise ValueError("执行必须同时显式指定 --execute 和 --isolated；默认仅校验")
    base_url = isolated_url(args.base_url)
    if not _text(args.username):
        raise ValueError("执行需要隔离实例的 root 账号")
    if (not math.isfinite(args.timeout) or args.timeout <= 0
            or not math.isfinite(args.min_interval) or args.min_interval < 0):
        raise ValueError("timeout 必须为有限正数，min_interval 必须为有限非负数")
    if not args.out:
        raise ValueError("执行必须显式指定新报告 --out")
    output = Path(args.out).resolve()
    if output == Path(args.cases).resolve():
        raise ValueError("报告不得覆盖原题文件")
    report = {"format": "rag-isolated-replay-report-v1", "generated_at": _now(), "base_url": base_url,
              "context_mode": "standalone", "isolated_declared": True, "run_status": "failed",
              "manual_review_required": True, "whole_answer_supported": None, "cases": []}
    # Reserve exclusively before credentials or HTTP: an old report can never be overwritten.
    with output.open("x", encoding="utf-8") as stream:
        try:
            password = os.environ.get(args.password_env) if args.password_env else None
            if not password:
                password = getpass.getpass("隔离 root 账号密码（不会写入报告）: ")
            client = IsolatedClient(base_url, args.timeout)
            client.request("/api/login", "POST", {"username": args.username, "password": password})
            previous_started = None
            for case in cases:
                if previous_started is not None:
                    time.sleep(max(0, args.min_interval - (time.monotonic() - previous_started)))
                started = previous_started = time.monotonic()
                payload = {key: case[key] for key in ("question", "scope_mode", "document_ids")}
                result = {"case": case, "request": payload, "execution_ok": False, "error": None,
                          "execution_status": "failed", "failure_stage": "manifest_before",
                          "heuristic_pass": False, "manual_review_required": True, "whole_answer_supported": None}
                try:
                    result["manifest_before"] = _check_manifest(client, case)
                    result["failure_stage"] = "query"
                    response = client.request("/api/query", "POST", payload)
                    result["response"] = response
                    if response.get("scope_mode") != "selected" or _ids(response.get("document_ids")) != case["document_ids"]:
                        raise RuntimeError("问答返回的文档范围与请求不一致")
                    if response.get("status") != "ok":
                        raise RuntimeError("问答未返回成功执行状态")
                    metrics = evaluate_case(case, response)
                    documents = {doc["document_id"]: doc for doc in case["scope_documents"]}
                    for source in response["sources"]:
                        if (not _id(source.get("document_id")) or source["document_id"] not in documents
                                or not _id(source.get("chunk_id"))
                                or source.get("filename") != documents[source["document_id"]]["filename"]):
                            raise RuntimeError("返回来源的文档或 chunk 身份与限定范围不一致")
                    result["failure_stage"] = "manifest_after"
                    result["manifest_after"] = _check_manifest(client, case)
                    result["heuristic_pass"] = metrics.pop("auto_pass")
                    metrics["answer_check"] = {"pass": "keyword_hit", "fail": "keyword_miss", "manual": "manual"}[metrics["answer_check"]]
                    result["metrics"] = metrics
                    result["execution_ok"] = True
                    result["execution_status"] = "completed"
                    result["failure_stage"] = None
                except (RuntimeError, ValueError, OSError) as exc:
                    result["error"] = str(exc)
                result["latency_ms"] = round((time.monotonic() - started) * 1000)
                report["cases"].append(result)
                print(f"{case['id']}: {'EXECUTED; MANUAL REVIEW' if result['execution_ok'] else 'FAILED'}")
            report["summary"] = {"cases": len(cases), "execution_ok": sum(item["execution_ok"] for item in report["cases"]),
                                 "heuristic_pass": sum(item["heuristic_pass"] for item in report["cases"])}
            report["run_status"] = "completed"
        except BaseException as exc:
            report["error"] = str(exc)
            raise
        finally:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--execute", action="store_true", help="默认仅校验；显式执行会在隔离实例生成新对话")
    parser.add_argument("--isolated", action="store_true", help="确认是独立 DB/上传目录/端口，不是生产服务")
    parser.add_argument("--base-url")
    parser.add_argument("--username", default="root")
    parser.add_argument("--password-env", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--min-interval", type=float, default=6.1)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(Path(args.cases))
        if not args.execute:
            print(f"仅格式校验通过：{len(cases)} 条；未登录、未发题、未验证资料内容")
            return 0
        report = run(args)
        print(f"报告已保存：{args.out}；所有新答案仍需人工整答审核")
        return int(any(not case["execution_ok"] for case in report["cases"]))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"隔离复测失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
