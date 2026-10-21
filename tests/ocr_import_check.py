"""OCR 输出导入隔离测试库：正文/引用/page 映射、suspect/failed 可见性。"""
from __future__ import annotations

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


def main() -> int:
    temp_root = ROOT.parent / ".tmp-tests"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ocr-import-check-", dir=temp_root) as temp:
        base = Path(temp)
        source = base / "sample.pdf"
        source.write_bytes(
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )
        import hashlib

        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        ocr = {
            "source": {"filename": source.name, "sha256": sha, "pages": 3},
            "pages": [
                {"page": 1, "status": "ok", "warnings": [], "text": "第一页：每日上限400元。"},
                {"page": 2, "status": "suspect", "warnings": ["low_confidence_line"],
                 "text": "第二页：温度应保持在18-27摄氏度。"},
                {"page": 3, "status": "failed", "warnings": ["ocr_failed"],
                 "text": "第三页：失败页正文不应入库。"},
            ],
        }
        ocr_path = base / "sample.ocr.json"
        ocr_path.write_text(json.dumps(ocr, ensure_ascii=False), encoding="utf-8")
        db = base / "rag.db"
        upload = base / "uploads"
        report = base / "report.json"
        command = [
            sys.executable, str(ROOT / "scripts" / "import_ocr_to_rag.py"),
            "--ocr-json", str(ocr_path), "--source", str(source),
            "--db", str(db), "--upload-dir", str(upload), "--report", str(report),
            "--question", "每日上限是多少", "--embedding-backend", "mock",
        ]
        env = dict(os.environ)
        env["RAG_EMBED_BACKEND"] = "mock"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(command, capture_output=True, text=True, timeout=180, env=env,
                                encoding="utf-8", errors="replace")
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
        check(payload["mapping_ok"], "检索来源正文/文件/页码与 DB 一致")
        check(not payload["failed_page_in_sources"], "检索来源不引用 failed 页")
        check(payload["source_ok"], "原 PDF 字节与 SHA-256 保留")
        check(any(check["sources"] for check in payload["checks"]), "检索确实返回来源")

        # SHA 不匹配必须拒绝导入。
        bad = dict(ocr)
        bad["source"] = dict(ocr["source"], sha256="0" * 64)
        bad_path = base / "bad.ocr.json"
        bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        bad_db = base / "bad.db"
        bad_result = subprocess.run([
            sys.executable, str(ROOT / "scripts" / "import_ocr_to_rag.py"),
            "--ocr-json", str(bad_path), "--source", str(source),
            "--db", str(bad_db), "--upload-dir", str(base / "bad-uploads"),
            "--report", str(base / "bad-report.json"), "--embedding-backend", "mock",
        ], capture_output=True, text=True, timeout=180, env=env,
            encoding="utf-8", errors="replace")
        check(bad_result.returncode != 0 and not bad_db.exists(), "SHA 不一致时拒绝导入")
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
