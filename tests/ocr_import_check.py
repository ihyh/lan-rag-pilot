"""OCR 输出导入隔离测试库：正文/引用/page 映射、来源与报告防误覆盖。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def pdf_bytes(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def main() -> int:
    temp_root = ROOT.parent / ".tmp-tests"
    temp_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["RAG_EMBED_BACKEND"] = "mock"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    def run_case(base: Path, source: Path, ocr: dict, *, report: Path | None = None,
                 question: list[str] | None = None, pages: list[int] | None = None,
                 tokens: list[str] | None = None, db_name: str = "rag.db") -> subprocess.CompletedProcess:
        ocr_path = base / f"{db_name}.ocr.json"
        ocr_path.write_text(json.dumps(ocr, ensure_ascii=False), encoding="utf-8")
        command = [
            sys.executable, str(ROOT / "scripts" / "import_ocr_to_rag.py"),
            "--ocr-json", str(ocr_path), "--source", str(source),
            "--db", str(base / db_name), "--upload-dir", str(base / f"{db_name}-uploads"),
            "--report", str(report or base / f"{db_name}-report.json"),
            "--embedding-backend", "mock",
        ]
        for question_text in question or []:
            command += ["--question", question_text]
        for page in pages or []:
            command += ["--expect-page", str(page)]
        for token in tokens or []:
            command += ["--expect-token", token]
        return subprocess.run(command, capture_output=True, text=True, timeout=180, env=env,
                              encoding="utf-8", errors="replace")

    def ocr_for(source: Path, texts: list[tuple[str, str]], *, missing_sha: bool = False,
                empty_sha: bool = False, wrong_sha: bool = False) -> dict:
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        expected = "" if empty_sha else ("0" * 64 if wrong_sha else sha)
        data = {
            "source": {"filename": source.name, "pages": len(texts)},
            "pages": [
                {"page": index, "status": status, "warnings": [], "text": text}
                for index, (status, text) in enumerate(texts, start=1)
            ],
        }
        if not missing_sha:
            data["source"]["sha256"] = expected
        return data

    with tempfile.TemporaryDirectory(prefix="ocr-import-check-", dir=temp_root) as temp:
        base = Path(temp)
        source = base / "three-pages.pdf"
        source.write_bytes(pdf_bytes(3))
        ocr = ocr_for(source, [
            ("ok", "第一页：每日上限400元。"),
            ("suspect", "第二页：温度应保持在18-27摄氏度。"),
            ("failed", "第三页：失败页正文不应入库。"),
        ])
        db = base / "rag.db"
        upload = base / "uploads"
        report = base / "report.json"
        result = run_case(base, source, ocr, report=report, question=["每日上限是多少"])
        check(result.returncode == 0, "Mock 嵌入下隔离导入成功")
        payload = json.loads(report.read_text(encoding="utf-8"))
        check(payload["pages"]["imported"] == [1, 2], "ok/suspect 页入库")
        check(payload["pages"]["skipped_failed"] == [3], "failed 页不入索引且可见")
        check(payload["pages"]["suspect"] == [2], "suspect 页在报告中可见")
        check("ocr_suspect_pages=2" in payload["tags"], "文档 tags 标记 suspect 页")
        check("ocr_failed_pages=3" in payload["tags"], "文档 tags 标记 failed 页")
        check(sorted(chunk["page"] for chunk in payload["chunks"]) == [1, 2],
              "chunk 页码只来自 ok/suspect 页")
        check(all(chunk["page"] != 3 for chunk in payload["chunks"]), "failed 页没有 chunk")
        check(payload["mapping_ok"], "检索来源正文/文件/页码/文档归属与 DB 一致")
        check(not payload["failed_page_in_sources"], "检索来源不引用 failed 页")
        check(payload["source_ok"], "原 PDF 字节与 SHA-256 保留")
        check(payload["source"]["pdf_pages"] == 3, "报告记录真实物理页数")
        check(any(check["sources"] for check in payload["checks"]), "检索确实返回来源")

        # I1：报告不能覆盖原件，也不能覆盖旧报告。
        original = source.read_bytes()
        for bad_report, label in ((source, "原件"), (report, "旧报告")):
            before = bad_report.read_bytes()
            result = run_case(base, source, ocr, report=bad_report, db_name=f"i1-{label}.db")
            check(result.returncode != 0, f"报告指向{label}时拒绝运行")
            check(bad_report.read_bytes() == before, f"拒绝后{label}字节不变")

        # I2：缺失/空/错误 SHA 都不得导入，也不得创建 DB。
        for variant, label in (({"missing_sha": True}, "缺失"),
                               ({"empty_sha": True}, "空值"),
                               ({"wrong_sha": True}, "错误")):
            candidate = ocr_for(source, [("ok", "正文"), ("ok", "正文"), ("ok", "正文")], **variant)
            result = run_case(base, source, candidate, db_name=f"i2-{label}.db")
            check(result.returncode != 0 and not (base / f"i2-{label}.db").exists(),
                  f"SHA {label}时拒绝导入且不建库")

        # I3：错误页码、重复页、缺页都必须拒绝。
        bad_page = ocr_for(source, [("ok", "a"), ("ok", "b"), ("ok", "c")])
        bad_page["pages"][1]["page"] = 7
        check(run_case(base, source, bad_page, db_name="i3-range.db").returncode != 0,
              "页码超出物理页数时拒绝")
        duplicate = ocr_for(source, [("failed", "失败正文"), ("ok", "正常正文"), ("ok", "第三页")])
        duplicate["pages"][1]["page"] = 1
        check(run_case(base, source, duplicate, db_name="i3-dup.db").returncode != 0,
              "重复页码拒绝合并/状态覆盖")
        missing = ocr_for(source, [("ok", "第一页"), ("ok", "第二页")])
        check(run_case(base, source, missing, db_name="i3-missing.db").returncode != 0,
              "缺页时拒绝把不完整结果当完整导入")

        # I4：复用同一库时，旧文档不能替新文档通过验收。
        old_source = base / "old.pdf"
        old_source.write_bytes(pdf_bytes(1))
        old_ocr = ocr_for(old_source, [("ok", "旧文档：限额 400 USD")])
        old_result = run_case(base, old_source, old_ocr, db_name="shared.db")
        check(old_result.returncode == 0, "旧文档先导入到同一隔离库")
        new_source = base / "new.pdf"
        new_source.write_bytes(pdf_bytes(2))
        new_ocr = ocr_for(new_source, [("ok", "新文档：限额 50 USD"), ("ok", "第二页")])
        new_report = base / "new-report.json"
        new_result = run_case(base, new_source, new_ocr, report=new_report,
                              question=["新文档限额是多少"], pages=[1], tokens=["50 USD"],
                              db_name="shared.db")
        new_payload = json.loads(new_report.read_text(encoding="utf-8"))
        check(new_result.returncode == 0, "新文档限定自身范围后验收通过")
        check(all(source_item["document_id"] == new_payload["source"]["document_id"]
                  for check_item in new_payload["checks"] for source_item in check_item["sources"]),
              "检索来源只属于新文档，旧文档不参与验收")

        # I5：页码与关键字必须在同一来源上同时满足。
        two_pages = base / "two-pages.pdf"
        two_pages.write_bytes(pdf_bytes(2))
        split_ocr = ocr_for(two_pages, [("ok", "标题页：没有金额"), ("ok", "第二页：限额 400 USD")])
        split_result = run_case(base, two_pages, split_ocr, report=base / "i5-bad-report.json",
                                question=["限额多少"], pages=[1], tokens=["400 USD"],
                                db_name="i5-bad.db")
        split_payload = json.loads((base / "i5-bad-report.json").read_text(encoding="utf-8"))
        check(split_result.returncode != 0
              and not split_payload["checks"][0]["expectation_hit"],
              "页码 1 与关键字只出现在页 2 时不通过")
        good_result = run_case(base, two_pages, split_ocr, report=base / "i5-good-report.json",
                               question=["限额多少"], pages=[2], tokens=["400 USD"],
                               db_name="i5-good.db")
        check(good_result.returncode == 0, "同一来源同时命中页码与关键字时通过")

        # 原文件 SHA 不一致必须拒绝。
        bad = ocr_for(source, [("ok", "正文"), ("ok", "正文"), ("ok", "正文")], wrong_sha=True)
        bad_result = run_case(base, source, bad, db_name="bad-sha.db")
        check(bad_result.returncode != 0 and not (base / "bad-sha.db").exists(),
              "SHA 不一致时拒绝导入")
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
