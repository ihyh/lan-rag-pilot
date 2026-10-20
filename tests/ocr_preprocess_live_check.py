"""OCR 预处理工具真实引擎样例检查（第二轮验收的实机部分）。

需要独立安装 requirements-ocr.txt；未安装时打印 SKIP 并以 0 退出，保证默认
`tests/run_checks.py` 不需要 OCR 依赖。检查使用**合成图片/PDF**，不使用私有文档：

- 中文、英文、数字与单位、表格/多栏、旋转页；
- 扫描 PDF、文字页与扫描页混合的 PDF；
- 逐页来源、页码映射、原件不变、置信度保留；
- 对照已知原文计算字符召回，记录实际差异，而不是只验"输出不为空"。

设置环境变量 OCR_LIVE_EVIDENCE_DIR 可把生成图片、结果 JSON 与摘要复制到指定目录，
便于人工对照原图复核。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _skip(reason: str) -> int:
    print(f"[SKIP] {reason}")
    return 0


def _find_font(candidates: list[str]) -> str | None:
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def _normalize(text: str) -> str:
    import re

    return re.sub(r"[\s，。；：、,.!?！？:;\"'“”‘’()（）\-—–_/]", "", text or "")


def _recall(expected: str, actual: str) -> float:
    normalized = _normalize(actual)
    characters = [char for char in _normalize(expected)]
    if not characters:
        return 1.0
    return sum(1 for char in characters if char in normalized) / len(characters)


def _escape_pdf_text(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if char in "()\\":
            out.append("\\" + char)
        elif 32 <= code <= 126:
            out.append(char)
        else:
            out.append("\\%03o" % code)
    return "".join(out)


def build_text_pdf(text: str) -> bytes:
    stream = ("BT /F1 12 Tf 72 720 Td (" + _escape_pdf_text(text) + ") Tj ET").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


def merge_pdfs(paths: list[Path]) -> bytes:
    from pypdf import PdfReader, PdfWriter

    import io

    writer = PdfWriter()
    for path in paths:
        for page in PdfReader(str(path)).pages:
            writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def main() -> int:
    try:
        from PIL import Image, ImageDraw, ImageFont
        import pypdfium2  # noqa: F401 - 只检查渲染依赖是否可用
    except ImportError as exc:
        return _skip("未安装 OCR 依赖（requirements-ocr.txt）：" + str(exc))
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import ocr_preprocess as tool
        engine = tool.RapidOcrEngine()
    except Exception as exc:  # noqa: BLE001 - 引擎不可用则跳过，不伪装通过
        return _skip(f"真实 OCR 引擎不可用：{type(exc).__name__}: {exc}")

    chinese_font = _find_font([
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ])
    latin_font = _find_font([
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ])
    if chinese_font is None or latin_font is None:
        return _skip("未找到可用的中文字体/西文字体，无法生成对照样例")

    evidence = os.environ.get("OCR_LIVE_EVIDENCE_DIR")
    work_dir = Path(tempfile.mkdtemp(prefix="ocr-live-"))
    results: list[str] = []
    failures = 0

    def check(condition: bool, message: str) -> None:
        nonlocal failures
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {message}")
        if not condition:
            failures += 1
        results.append(("[PASS]" if condition else "[FAIL]") + " " + message)

    try:
        # ---------------- 合成样例 ----------------
        def make_image(name: str, lines: list[tuple[str, str, int]], size=(1200, 560), columns=False):
            image = Image.new("RGB", size, "white")
            draw = ImageDraw.Draw(image)
            if columns:
                left = ImageFont.truetype(chinese_font, 40)
                draw.text((50, 80), lines[0][0], fill="black", font=left)
                draw.text((640, 80), lines[0][1], fill="black", font=left)
                draw.text((50, 220), lines[1][0], fill="black", font=left)
                draw.text((640, 220), lines[1][1], fill="black", font=left)
            else:
                y = 60
                for text, font_path, font_size in lines:
                    draw.text((60, y), text, fill="black",
                              font=ImageFont.truetype(font_path, font_size))
                    y += font_size + 60
            path = work_dir / name
            image.save(path)
            return path

        chinese_text = ["设备巡检记录", "出差住宿每日上限 400 元", "温度应保持在 18-27 摄氏度"]
        english_text = ["Hotel reimbursement limit is 400 yuan per day.", "UPS battery inspection is quarterly."]
        table_text = ["左列：机房温度 18-27 摄氏度", "右列：UPS 巡检每季度一次"]
        chinese_png = make_image(
            "chinese.png",
            [(text, chinese_font, 52) for text in chinese_text],
        )
        english_png = make_image(
            "english.png",
            [(text, latin_font, 46) for text in english_text],
            size=(1200, 400),
        )
        table_png = make_image(
            "table.png",
            [("左列：机房温度 18-27 摄氏度", "右列：UPS 巡检每季度一次"),
             ("左列：核心交换机每月一次", "右列：负责人：运维组")],
            columns=True,
        )
        rotated_png = work_dir / "rotated.png"
        with Image.open(chinese_png) as base:
            base.rotate(-90, expand=True).save(rotated_png)

        chinese_pdf = work_dir / "chinese.pdf"
        with Image.open(chinese_png) as image:
            image.save(chinese_pdf, "PDF", resolution=200.0)
        english_pdf = work_dir / "english.pdf"
        with Image.open(english_png) as image:
            image.save(english_pdf, "PDF", resolution=200.0)
        table_pdf = work_dir / "table.pdf"
        with Image.open(table_png) as image:
            image.save(table_pdf, "PDF", resolution=200.0)
        rotated_pdf = work_dir / "rotated.pdf"
        with Image.open(rotated_png) as image:
            image.save(rotated_pdf, "PDF", resolution=200.0)
        text_only_pdf = work_dir / "text-layer.pdf"
        text_only_pdf.write_bytes(build_text_pdf(
            "This page keeps a real embedded text layer with enough characters to be preserved."
        ))
        mixed_pdf = work_dir / "mixed.pdf"
        mixed_pdf.write_bytes(merge_pdfs([text_only_pdf, chinese_pdf]))

        originals = {path: tool.sha256_file(path) for path in (
            chinese_png, english_png, table_png, rotated_png,
            chinese_pdf, english_pdf, table_pdf, rotated_pdf, text_only_pdf, mixed_pdf,
        )}

        # ---------------- 运行工具 ----------------
        options = tool.OcrOptions()
        code, manifest = tool.run(
            [mixed_pdf, english_pdf, table_pdf, rotated_pdf],
            work_dir / "out",
            options,
            engine=engine,
        )
        result_paths = {path.stem: work_dir / "out" / f"{path.stem}.ocr.json" for path in (
            mixed_pdf, english_pdf, table_pdf, rotated_pdf,
        )}
        results_by_name = {
            name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in result_paths.items()
        }
        check(code == 0, "真实引擎处理退出码为 0")
        check(all(result["status"] in ("ok", "partial") for result in results_by_name.values()),
              "所有样例文件状态非 failed")

        # ---------------- 逐项验收 ----------------
        mixed = results_by_name["mixed"]["pages"]
        check([page["source"] for page in mixed] == ["text_layer", "ocr"],
              f"混合 PDF 按页分流（实际 {[page['source'] for page in mixed]}）")
        check(mixed[0]["text"].strip().startswith("This page keeps a real embedded text layer"),
              "可用文字层原样保留")
        check(mixed[1].get("confidence", {}).get("mean", 0) > 0.5,
              "真实引擎置信度被保留（未伪造）")
        mixed_text = (work_dir / "out" / "mixed.ocr.txt").read_text(encoding="utf-8")
        check(mixed_text.count("\f") == 1, "混合 PDF 文本导出页码映射正确")

        chinese_ocr = results_by_name["mixed"]["pages"][1]["text"]
        chinese_recall = _recall("".join(chinese_text), chinese_ocr)
        check(chinese_recall >= 0.85, f"中文样例字符召回 {chinese_recall:.3f} >= 0.85")
        check("400" in chinese_ocr and "18" in chinese_ocr and "27" in chinese_ocr,
              "中文样例中的数字被识别（400/18/27）")

        english_ocr = results_by_name["english"]["pages"][0]["text"]
        english_recall = _recall(" ".join(english_text), english_ocr)
        check(english_recall >= 0.9, f"英文样例字符召回 {english_recall:.3f} >= 0.9")

        table_ocr = results_by_name["table"]["pages"][0]["text"]
        for token in ("左列", "右列", "机房温度", "UPS", "每季度"):
            check(token in table_ocr, f"表格/多栏样例保留 {token}")

        rotated_page = results_by_name["rotated"]["pages"][0]
        rotated_ocr = rotated_page["text"]
        rotated_recall = _recall("".join(chinese_text), rotated_ocr)
        # 引擎可能自带方向分类，直接在 0 度就读对；也可能需要工具尝试旋转。
        check(rotated_page.get("rotation_degrees") in (0, 90, 180, 270),
              f"旋转页记录识别方向（实际 {rotated_page.get('rotation_degrees')} 度）")
        check(bool(rotated_page.get("rotation_attempts")), "旋转页记录 OCR 尝试次数")
        check(rotated_recall >= 0.7, f"旋转页字符召回 {rotated_recall:.3f} >= 0.7")

        checksums = {path: tool.sha256_file(path) for path in originals}
        check(checksums == originals, "所有原件 SHA-256 均未改变")
        for name, result in results_by_name.items():
            check(result["summary"]["failed_pages"] == 0, f"{name} 无失败页")
            check(result["tool"]["version"] == tool.TOOL_VERSION, f"{name} 记录工具版本")
            check(bool(result["engine"] and result["engine"].get("package_version")),
                  f"{name} 记录 OCR 引擎/版本")

        # ---------------- 证据归档（可选） ----------------
        summary = {
            "engine": engine.info(),
            "samples": {
                "chinese": {"text": chinese_ocr, "recall": round(chinese_recall, 4)},
                "english": {"text": english_ocr, "recall": round(english_recall, 4)},
                "table": {"text": table_ocr},
                "rotated": {"text": rotated_ocr, "recall": round(rotated_recall, 4),
                            "rotation_degrees": rotated_page.get("rotation_degrees")},
                "mixed_pages": [page["source"] for page in mixed],
            },
            "results": results,
            "work_dir": str(work_dir),
            "manifest_exit_code": code,
        }
        print("\n样例对照摘要：")
        print(json.dumps(summary["samples"], ensure_ascii=False, indent=2))
        if evidence:
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            for path in work_dir.glob("*.png"):
                shutil.copy2(path, target / path.name)
            for path in (work_dir / "out").glob("*"):
                shutil.copy2(path, target / path.name)
            (target / "ocr-live-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"证据已复制到 {target}")
    finally:
        if failures or not evidence:
            shutil.rmtree(work_dir, ignore_errors=True)
        else:
            print(f"证据临时目录保留在 {work_dir}（已复制到 {evidence}）")

    print(f"\n结果: {len(results) - failures} 通过, {failures} 失败")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
