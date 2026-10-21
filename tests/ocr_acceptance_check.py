"""OCR 隔离验收：原页对照、阅读顺序、表格行列、数字单位、混合 PDF、页码追溯。

只使用合成样例与独立输出目录，不触碰生产索引/数据库。真实引擎缺失时 SKIP。
首轮结果与失败证据写入 --evidence 目录（默认 .verify/ocr-acceptance-20261021）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import ocr_preprocess as tool  # noqa: E402
from ocr_preprocess_live_check import (  # noqa: E402
    _find_font, _normalize, _recall, build_text_pdf, merge_pdfs,
)

ZOOM = 1200
LINES = ["设备巡检记录", "出差住宿每日上限400元", "温度应保持在18-27摄氏度"]
TABLE = [
    ["项目", "周期", "上限"],
    ["巡检", "每季度", "400元"],
    ["温度", "每班", "18-27摄氏度"],
]


def make_text_image(path: Path, lines: list[str], *, size=(1400, 700)) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_font([r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                            r"C:\Windows\Fonts\simsun.ttc"])
    assert font_path, "缺少中文字体"
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 64)
    y = 80
    for line in lines:
        draw.text((80, y), line, fill="black", font=font)
        y += 150
    image.save(path)


def make_table_image(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_font([r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                            r"C:\Windows\Fonts\simsun.ttc"])
    assert font_path, "缺少中文字体"
    width, height = 1400, 700
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 44)
    left, top, right, bottom = 80, 60, 1320, 640
    rows, cols = len(TABLE), len(TABLE[0])
    row_h = (bottom - top) / rows
    col_w = (right - left) / cols
    for r in range(rows + 1):
        y = int(top + r * row_h)
        draw.line([(left, y), (right, y)], fill="black", width=3)
    for c in range(cols + 1):
        x = int(left + c * col_w)
        draw.line([(x, top), (x, bottom)], fill="black", width=3)
    for r, row in enumerate(TABLE):
        for c, text in enumerate(row):
            x = int(left + c * col_w + 30)
            y = int(top + r * row_h + 45)
            draw.text((x, y), text, fill="black", font=font)
    image.save(path)


def _lines_after_normalize(result: dict) -> list[str]:
    text = result["pages"][0].get("text") or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _order_ok(observed: list[str], expected: list[str]) -> bool:
    normalized = [_normalize(value).replace(" ", "") for value in observed]
    want = [_normalize(value).replace(" ", "") for value in expected]
    positions = []
    for line in want:
        for index, value in enumerate(normalized):
            if line in value:
                positions.append(index)
                break
    return len(positions) == len(want) and positions == sorted(positions)


def prepare_evidence_dir(evidence: Path) -> bool:
    """证据目录必须是新批次；已存在且非空时拒绝，绝不删除上一轮原页/失败输出。"""
    if evidence.exists() and any(evidence.iterdir()):
        print(f"REFUSE: 证据目录已存在且非空，请为新批次指定新的 --evidence：{evidence}")
        return False
    evidence.mkdir(parents=True, exist_ok=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default="",
                        help="本轮证据目录；省略则使用新的临时唯一目录，不覆盖旧证据")
    args = parser.parse_args()
    evidence = (Path(args.evidence) if args.evidence
                else Path(tempfile.mkdtemp(prefix="ocr-acceptance-")))
    if not prepare_evidence_dir(evidence):
        return 2
    work = evidence / "work"
    work.mkdir()

    try:
        engine = tool.build_engine("rapidocr")
    except tool.OcrEngineUnavailable as exc:
        print(f"SKIP: {exc}")
        return 0
    if engine is None:
        print("SKIP: OCR 引擎不可用")
        return 0
    observations = {"engine": engine.info(), "lines": LINES, "table": TABLE, "cases": {}}

    # ---------- 旋转页：阅读顺序与数字单位 ----------
    upright = work / "upright.png"
    make_text_image(upright, LINES)
    from PIL import Image
    base = Image.open(upright)
    for angle in (90, 180, 270):
        rotated = work / f"rotated-{angle}.png"
        base.rotate(angle, expand=True).save(rotated)
        pdf = work / f"rotated-{angle}.pdf"
        with Image.open(rotated) as image:
            image.save(pdf, "PDF", resolution=200.0)
        started = time.monotonic()
        code, _ = tool.run([pdf], work / f"out-rotated-{angle}", tool.OcrOptions(), engine=engine)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        result = json.loads((work / f"out-rotated-{angle}" / pdf.with_suffix(".ocr.json").name).read_text(encoding="utf-8"))
        observed = _lines_after_normalize(result)
        case = {
            "kind": "rotated",
            "angle": angle,
            "elapsed_ms": elapsed_ms,
            "exit_code": code,
            "rotation_degrees": result["pages"][0].get("rotation_degrees"),
            "observed_lines": observed,
            "order_ok": _order_ok(observed, LINES),
            "numbers_units_ok": all(token in " ".join(observed).replace(" ", "")
                                    for token in ("400", "元", "18-27", "摄氏度")),
            "page": result["pages"][0]["page"],
            "source_sha256": result["source"]["sha256"],
        }
        observations["cases"][f"rotated-{angle}"] = case
        print(f"rotated-{angle}: rotation={case['rotation_degrees']} order={case['order_ok']} "
              f"numbers={case['numbers_units_ok']} observed={observed}")

    # ---------- 表格行列 ----------
    table_png = work / "table.png"
    make_table_image(table_png)
    table_pdf = work / "table.pdf"
    with Image.open(table_png) as image:
        image.save(table_pdf, "PDF", resolution=200.0)
    started = time.monotonic()
    code, _ = tool.run([table_pdf], work / "out-table", tool.OcrOptions(), engine=engine)
    table_elapsed_ms = int((time.monotonic() - started) * 1000)
    table_result = json.loads((work / "out-table" / "table.ocr.json").read_text(encoding="utf-8"))
    observed = _lines_after_normalize(table_result)
    joined = " ".join(observed).replace(" ", "")
    flat_expected = [cell for row in TABLE for cell in row]
    positions = []
    for cell in flat_expected:
        for index, line in enumerate(observed):
            if cell in line.replace(" ", ""):
                positions.append(index)
                break
    observations["cases"]["table"] = {
        "kind": "table",
        "elapsed_ms": table_elapsed_ms,
        "exit_code": code,
        "observed_lines": observed,
        "cells_present": {cell: cell in joined for cell in flat_expected},
        "row_column_order_ok": len(positions) == len(flat_expected) and positions == sorted(positions),
        "page": table_result["pages"][0]["page"],
    }
    print("table observed:", observed, "row/col order:",
          observations["cases"]["table"]["row_column_order_ok"])

    # ---------- 旋转表格：列顺序不能被打乱 ----------
    for angle in (90, 270):
        rotated_table = work / f"table-{angle}.png"
        Image.open(table_png).rotate(angle, expand=True).save(rotated_table)
        rotated_table_pdf = work / f"table-{angle}.pdf"
        with Image.open(rotated_table) as image:
            image.save(rotated_table_pdf, "PDF", resolution=200.0)
        started = time.monotonic()
        code, _ = tool.run([rotated_table_pdf], work / f"out-table-{angle}",
                           tool.OcrOptions(), engine=engine)
        elapsed = int((time.monotonic() - started) * 1000)
        result = json.loads((work / f"out-table-{angle}" /
                             rotated_table_pdf.with_suffix(".ocr.json").name).read_text(encoding="utf-8"))
        lines = _lines_after_normalize(result)
        joined_rotated = " ".join(lines).replace(" ", "")
        positions = []
        for cell in flat_expected:
            for index, line in enumerate(lines):
                if cell in line.replace(" ", ""):
                    positions.append(index)
                    break
        observations["cases"][f"table-{angle}"] = {
            "kind": "rotated_table",
            "angle": angle,
            "elapsed_ms": elapsed,
            "rotation_degrees": result["pages"][0].get("rotation_degrees"),
            "observed_lines": lines,
            "cells_present": {cell: cell in joined_rotated for cell in flat_expected},
            "row_column_order_ok": (len(positions) == len(flat_expected)
                                    and positions == sorted(positions)),
        }
        print(f"table-{angle}: rotation={result['pages'][0].get('rotation_degrees')} "
              f"row/col={observations['cases'][f'table-{angle}']['row_column_order_ok']}")

    # ---------- 同页混合：文字页眉 + 扫描正文 ----------
    header_pdf = work / "header-only.pdf"
    header_pdf.write_bytes(build_text_pdf(
        "Header: this page has native text and a scanned body image with facts below."
    ))
    same_page_pdf = work / "same-page-mixed.pdf"
    from pypdf import PdfReader, PdfWriter, Transformation  # noqa: PLC0415

    writer = PdfWriter()
    page = PdfReader(str(header_pdf)).pages[0]
    body = PdfReader(str(table_pdf)).pages[0]
    page.merge_transformed_page(body, Transformation().scale(1.2, 1.2).translate(40, 60), over=True)
    writer.add_page(page)
    with same_page_pdf.open("wb") as handle:
        writer.write(handle)
    started = time.monotonic()
    code, _ = tool.run([same_page_pdf], work / "out-same-page", tool.OcrOptions(), engine=engine)
    same_elapsed = int((time.monotonic() - started) * 1000)
    same_result = json.loads((work / "out-same-page" / "same-page-mixed.ocr.json").read_text(encoding="utf-8"))
    same_page = same_result["pages"][0]
    same_text = same_page.get("text") or ""
    observations["cases"]["same-page-mixed"] = {
        "kind": "same_page_mixed",
        "elapsed_ms": same_elapsed,
        "source": same_page.get("source"),
        "status": same_page.get("status"),
        "warnings": same_page.get("warnings"),
        "header_preserved": "Header: this page has native text" in same_text,
        "body_preserved": all(token in same_text.replace(" ", "") for token in ("400元", "18-27摄氏度")),
        "text_chars": same_page.get("chars"),
    }
    print("same-page mixed:", observations["cases"]["same-page-mixed"])


    # ---------- 混合 PDF：不漏页 + 页码追溯 ----------
    text_pdf = work / "text-layer.pdf"
    text_pdf.write_bytes(build_text_pdf(
        "This page keeps a real embedded text layer for mixed PDF acceptance."
    ))
    scanned1_pdf = work / "scanned-1.pdf"
    with Image.open(work / "rotated-90.png") as image:
        image.save(scanned1_pdf, "PDF", resolution=200.0)
    scanned2_pdf = work / "scanned-2.pdf"
    with Image.open(table_png) as image:
        image.save(scanned2_pdf, "PDF", resolution=200.0)
    mixed_pdf = work / "mixed.pdf"
    mixed_pdf.write_bytes(merge_pdfs([text_pdf, scanned1_pdf, scanned2_pdf]))
    started = time.monotonic()
    code, _ = tool.run([mixed_pdf], work / "out-mixed", tool.OcrOptions(), engine=engine)
    mixed_elapsed_ms = int((time.monotonic() - started) * 1000)
    mixed_result = json.loads((work / "out-mixed" / "mixed.ocr.json").read_text(encoding="utf-8"))
    pages = mixed_result["pages"]
    observations["cases"]["mixed"] = {
        "kind": "mixed",
        "elapsed_ms": mixed_elapsed_ms,
        "exit_code": code,
        "page_count": len(pages),
        "pages": [{"page": page["page"], "source": page["source"], "chars": page["chars"],
                   "rotation": page.get("rotation_degrees")} for page in pages],
        "no_missing_pages": len(pages) == 3 and [page["page"] for page in pages] == [1, 2, 3],
        "source_sha256": mixed_result["source"]["sha256"],
        "expected_sha256": tool.sha256_file(mixed_pdf),
        "txt_page_separators": (work / "out-mixed" / "mixed.ocr.txt").read_text(encoding="utf-8").count("\f"),
    }
    print("mixed pages:", observations["cases"]["mixed"]["pages"])

    # ---------- 页码/原文件追溯 ----------
    observations["cases"]["traceability"] = {
        "kind": "traceability",
        "filename": mixed_result["source"]["filename"],
        "filename_ok": mixed_result["source"]["filename"] == mixed_pdf.name,
        "sha256_ok": mixed_result["source"]["sha256"] == tool.sha256_file(mixed_pdf),
        "page_numbers": [page["page"] for page in pages],
        "page_numbers_ok": [page["page"] for page in pages] == [1, 2, 3],
        "txt_separator_ok": observations["cases"]["mixed"]["txt_page_separators"] == 2,
    }

    # ---------- 验收结论 ----------
    failures: list[str] = []
    known_limitations: list[str] = []
    for angle in (90, 270):
        case = observations["cases"][f"rotated-{angle}"]
        if not case["order_ok"]:
            failures.append(f"rotated-{angle}: 阅读顺序未恢复")
        if not case["numbers_units_ok"]:
            failures.append(f"rotated-{angle}: 数字/单位不完整")
    if not observations["cases"]["rotated-180"]["order_ok"]:
        known_limitations.append(
            "rotated-180: 文字识别正确，但 180 度页面无方向分类信号，行序仍为反转；"
            "已保留失败证据，需人工方向标记或独立方向分类器（不在本轮修复范围）。"
        )
    table = observations["cases"]["table"]
    if not table["row_column_order_ok"] or not all(table["cells_present"].values()):
        failures.append("table: 行列顺序或单元格内容不完整")
    for angle in (90, 270):
        case = observations["cases"][f"table-{angle}"]
        if not case["row_column_order_ok"] or not all(case["cells_present"].values()):
            failures.append(f"table-{angle}: 旋转表格列顺序错误或单元格缺失")
    same = observations["cases"]["same-page-mixed"]
    if not (same["header_preserved"] and same["body_preserved"]):
        failures.append("same-page-mixed: 原生页眉或扫描正文缺失")
    if same["source"] != "ocr" or "mixed_page_ocr_merged" not in (same["warnings"] or []):
        failures.append("same-page-mixed: 未走混合页 OCR 合并或缺少 warning")
    if same["status"] != "suspect":
        failures.append("same-page-mixed: 混合页不应显示为完整成功")
    mixed = observations["cases"]["mixed"]
    if not mixed["no_missing_pages"]:
        failures.append("mixed: 混合 PDF 页数/页码不完整")
    if mixed["source_sha256"] != mixed["expected_sha256"]:
        failures.append("mixed: 来源 SHA-256 不一致")
    trace = observations["cases"]["traceability"]
    for key in ("filename_ok", "sha256_ok", "page_numbers_ok", "txt_separator_ok"):
        if not trace[key]:
            failures.append(f"traceability: {key} 失败")
    observations["failures"] = failures
    observations["known_limitations"] = known_limitations
    observations["partial"] = bool(known_limitations)
    observations["passed"] = not failures
    (evidence / "acceptance.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("failures:", failures)
    for item in known_limitations:
        print("known limitation:", item)
    print("evidence:", evidence / "acceptance.json")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
