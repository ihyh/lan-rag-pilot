"""OCR 预处理工具离线回归：注入假引擎/假渲染器，不需要安装任何 OCR 依赖。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ocr_preprocess as tool  # noqa: E402

LONG_TEXT = (
    "This page has a real embedded text layer. It is long enough to be treated as usable "
    "and must never be sent to OCR, because the original text layer has to be preserved."
)

PASS: list[str] = []
FAIL: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        PASS.append(message)
        print(f"  [PASS] {message}")
    else:
        FAIL.append(message)
        print(f"  [FAIL] {message}")


# ---------------------------------------------------------------------------
# 最小 PDF 构造（多页；text=None 表示该页没有文字层）
# ---------------------------------------------------------------------------


def _escape_pdf_text(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if char in "()\\":
            out.append("\\" + char)
        elif code < 32 or code > 126:
            out.append("\\%03o" % code)
        else:
            out.append(char)
    return "".join(out)


def build_pdf(pages: list[str | None]) -> bytes:
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(pages):
        if text:
            stream = (
                "BT /F1 12 Tf 72 720 Td (" + _escape_pdf_text(text) + ") Tj ET"
            ).encode("latin-1")
            content = (
                b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
                + stream + b"\nendstream"
            )
        else:
            content = b"<< /Length 0 >>\nstream\n\nendstream"
        page_object = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>"
        ).encode("ascii")
        objects.append(page_object)
        objects.append(content)
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n"
        f"{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)


class FakeImage:
    def __init__(self, angle: int = 0, frame: int = 0) -> None:
        self.angle = angle % 360
        self.frame = frame

    def rotate(self, angle: int, expand: bool = True):
        return FakeImage(self.angle + angle, self.frame)


class FakeRenderer:
    page_count = 1

    def __init__(self, image: FakeImage | None = None) -> None:
        self.image = image or FakeImage()
        self.closed = False

    def render(self, index: int):
        return self.image

    def close(self) -> None:
        self.closed = True


class MultiFrameRenderer:
    """模拟两帧 TIFF：按帧号返回不同的 FakeImage。"""

    page_count = 2

    def __init__(self) -> None:
        self.rendered: list[int] = []
        self.closed = False
        self.fail_second = False

    def render(self, index: int):
        self.rendered.append(index)
        if index == 1 and self.fail_second:
            raise RuntimeError("simulated frame decode failure")
        return FakeImage(frame=index)

    def close(self) -> None:
        self.closed = True


class FakeEngine:
    name = "fake"

    def __init__(self, lines_for=None, fail_calls: set[int] | None = None) -> None:
        self.calls = 0
        self.angles: list[int] = []
        self._lines_for = lines_for or (lambda image: [tool.OcrLine("识别出的文字内容足够长，可以正常通过。", 0.95)])
        self._fail_calls = fail_calls or set()

    def info(self) -> dict:
        return {
            "name": self.name,
            "package": "fake",
            "package_version": "0.0.0",
            "runtime": "test",
            "runtime_version": "0.0.0",
            "models": [],
            "notes": "测试用假引擎",
        }

    def recognize(self, image):
        self.calls += 1
        self.angles.append(getattr(image, "angle", 0))
        if self.calls in self._fail_calls:
            raise RuntimeError("simulated engine failure")
        return list(self._lines_for(image))


def renderer_factory(image: FakeImage | None = None):
    def factory(path: Path, options) -> FakeRenderer:
        return FakeRenderer(image)
    return factory


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def with_tmp(prefix: str = "ocr-check-"):
    return tempfile.TemporaryDirectory(prefix=prefix)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_tool(inputs, out_dir, options=None, engine=None, factory=None, overwrite=False):
    options = options or tool.OcrOptions()
    return tool.run(
        [Path(item) for item in inputs],
        Path(out_dir),
        options,
        engine=engine,
        renderer_factory=factory,
        overwrite=overwrite,
    )


def main() -> None:
    print("\n== 文字层可用：不应调用 OCR ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "text.pdf"
        pdf.write_bytes(build_pdf([LONG_TEXT]))
        engine = FakeEngine()
        code, manifest = run_tool([pdf], root / "out", engine=engine)
        result = read_json(root / "out" / "text.ocr.json")
        ensure(code == 0, "文字层 PDF 退出码应为 0")
        ensure(engine.calls == 0, "可用文字层不得调用 OCR")
        ensure(result["pages"][0]["source"] == "text_layer", "来源应标记 text_layer")
        ensure(result["pages"][0]["status"] == "ok", "文字层页面状态 ok")
        ensure(result["summary"]["text_layer_pages"] == 1, "汇总文字层页数")
        ensure(result["summary"]["ocr_pages"] == 0, "汇总 OCR 页数")
        ensure(result["source"]["pages"] == 1, "来源摘要保留页数")
        ensure(result["engine"]["name"] == "fake", "记录引擎信息")
        text_output = (root / "out" / "text.ocr.txt").read_text(encoding="utf-8")
        ensure("embedded text layer" in text_output, "文本导出保留原文")
        check(True, "可用文字层原样保留、不调用 OCR、来源摘要完整")

    print("\n== 扫描页：OCR 并记录置信度 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "scan.pdf"
        pdf.write_bytes(build_pdf([None]))
        engine = FakeEngine(lambda image: [
            tool.OcrLine("扫描件第一行文字", 0.98),
            tool.OcrLine("第二行 400 元", 0.91),
        ])
        code, _ = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        result = read_json(root / "out" / "scan.ocr.json")
        page = result["pages"][0]
        ensure(code == 0 and page["source"] == "ocr", "扫描页应走 OCR")
        ensure(engine.calls == 1, "扫描页调用一次 OCR")
        ensure(page["confidence"]["mean"] > 0.9, "保留引擎给出的置信度")
        ensure(page["text"].startswith("扫描件第一行文字"), "OCR 文本按行拼接")
        check(True, "扫描页 OCR、逐行置信度与文本输出正确")

    print("\n== 混合 PDF：按页分流 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "mixed.pdf"
        pdf.write_bytes(build_pdf([LONG_TEXT, None, LONG_TEXT]))
        engine = FakeEngine(lambda image: [tool.OcrLine("中间这一页是扫描图，需要 OCR。", 0.93)])
        code, _ = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        result = read_json(root / "out" / "mixed.ocr.json")
        sources = [page["source"] for page in result["pages"]]
        ensure(code == 0, "混合 PDF 退出码 0")
        ensure(sources == ["text_layer", "ocr", "text_layer"], f"按页判断来源，实际 {sources}")
        ensure(engine.calls == 1, "只对被扫描页调用 OCR")
        ensure(result["summary"]["text_layer_pages"] == 2, "汇总保留两页文字层")
        exported = (root / "out" / "mixed.ocr.txt").read_text(encoding="utf-8")
        ensure(exported.count("\f") == 2, "文本导出用分页符映射页码")
        check(True, "混合 PDF 按页分流且页码映射正确")

    print("\n== 引擎没有置信度时不得伪造 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "noscore.pdf"
        pdf.write_bytes(build_pdf([None]))
        engine = FakeEngine(lambda image: [tool.OcrLine("没有置信度的引擎输出内容", None)])
        run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        page = read_json(root / "out" / "noscore.ocr.json")["pages"][0]
        ensure("confidence" not in page, "无置信度时不写 confidence 字段")
        ensure("uncertain_lines" not in page, "无置信度时不伪造不确定行列表")
        check(True, "引擎无置信度时输出不伪造分数")

    print("\n== 可疑页：低置信度/乱码/空页明确标记 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "weak.pdf"
        pdf.write_bytes(build_pdf([None]))
        engine = FakeEngine(lambda image: [
            tool.OcrLine("低置信度的文本行", 0.31),
            tool.OcrLine("第二行内容", 0.42),
        ])
        code, _ = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        page = read_json(root / "out" / "weak.ocr.json")["pages"][0]
        ensure(code == 0, "可疑页不算失败退出码")
        ensure(page["status"] == "suspect", "低置信度页状态 suspect")
        ensure("low_confidence" in page["warnings"], "标记 low_confidence")
        ensure(len(page["uncertain_lines"]) == 2, "低分行进入 uncertain_lines")
        ensure(all(line["score"] < 0.6 for line in page["uncertain_lines"]), "uncertain_lines 带真实分数")
        check(True, "低置信度页标记可疑并列出待核对行")

    print("\n== 高均分 + 低分关键行：整页必须标记可疑 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "hidden-low.pdf"
        pdf.write_bytes(build_pdf([None]))
        lines = [tool.OcrLine(f"第 {index} 行都是高置信度文本", 0.99) for index in range(1, 10)]
        lines.append(tool.OcrLine("压力不得超过 0.6 MPa", 0.55))
        engine = FakeEngine(lambda image: lines)
        code, _ = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        result = read_json(root / "out" / "hidden-low.ocr.json")
        page = result["pages"][0]
        ensure(code == 0, "低分行不改变退出码（可疑页不算失败）")
        ensure(page["confidence"]["mean"] > 0.9, "页面均分确实高于阈值")
        ensure(page["status"] == "suspect", "存在低分行时整页必须 suspect")
        ensure("low_confidence_line" in page["warnings"], "标记 low_confidence_line")
        ensure("low_confidence" not in page["warnings"], "均分高时不误报 low_confidence")
        ensure(len(page["uncertain_lines"]) == 1
               and page["uncertain_lines"][0]["score"] < 0.6, "保留低分行原文与真实分数")
        ensure(result["status"] == "partial", "文件整体 partial")
        check(True, "低分关键行不会被页面均分掩盖")

    print("\n== 多帧 TIFF：逐帧处理且页码映射完整 ==")
    with with_tmp() as temp:
        root = Path(temp)
        tiff = root / "scan.tif"
        tiff.write_bytes(b"fake-tiff-bytes")  # 假渲染器不读真实图像内容

        def lines_for(image):
            if image.frame == 1:
                return [tool.OcrLine("第二帧文字内容足够长可以识别。", 0.92)]
            return [tool.OcrLine("第一帧文字内容足够长可以识别。", 0.95)]

        renderer = MultiFrameRenderer()
        code, _ = run_tool(
            [tiff], root / "out", engine=FakeEngine(lines_for),
            factory=lambda path, options: renderer,
        )
        result = read_json(root / "out" / "scan.ocr.json")
        ensure(code == 0, "多帧 TIFF 退出码 0")
        ensure(result["source"]["pages"] == 2, "来源摘要记录 2 帧")
        ensure([page["page"] for page in result["pages"]] == [1, 2], "页码映射为 1/2")
        ensure([page["source"] for page in result["pages"]] == ["ocr", "ocr"], "两帧都走 OCR")
        ensure("第一帧" in result["pages"][0]["text"] and "第二帧" in result["pages"][1]["text"],
               "两帧文字都被保留，没有静默丢弃第二帧")
        exported = (root / "out" / "scan.ocr.txt").read_text(encoding="utf-8")
        ensure(exported.count("\f") == 1, "两帧文本用分页符映射")

        code, _ = run_tool(
            [tiff], root / "out-pages", engine=FakeEngine(lines_for),
            factory=lambda path, options: MultiFrameRenderer(),
            options=tool.OcrOptions(pages={2}),
        )
        result = read_json(root / "out-pages" / "scan.ocr.json")
        ensure(result["pages"][0]["status"] == "skipped", "--pages 2 时第一帧 skipped")
        ensure(result["pages"][1]["source"] == "ocr", "--pages 2 时第二帧正常 OCR")

        code, _ = run_tool(
            [tiff], root / "out-limit", engine=FakeEngine(lines_for),
            factory=lambda path, options: MultiFrameRenderer(),
            options=tool.OcrOptions(max_pages=1),
        )
        result = read_json(root / "out-limit" / "scan.ocr.json")
        ensure(result["pages"][1]["status"] == "skipped"
               and "page_limit_reached" in result["pages"][1]["warnings"], "页数上限作用于多帧")

        failing_renderer = MultiFrameRenderer()
        failing_renderer.fail_second = True
        code, _ = run_tool(
            [tiff], root / "out-fail", engine=FakeEngine(lines_for),
            factory=lambda path, options: failing_renderer,
        )
        result = read_json(root / "out-fail" / "scan.ocr.json")
        ensure(code == 1, "第二帧解码失败时退出码 1")
        ensure(result["pages"][0]["status"] == "ok"
               and result["pages"][1]["status"] == "failed"
               and result["pages"][1]["error"]["code"] == "render_failed", "第二帧失败被明确标记")
        check(True, "多帧 TIFF 逐帧处理、页选择/上限/失败路径正确")

    print("\n== 文字层疑似乱码：回退 OCR 并保留审计 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "garbled.pdf"
        garbled = "正常片段" + ("\x01" * 40) + "后续文字" * 10
        pdf.write_bytes(build_pdf([garbled]))
        engine = FakeEngine(lambda image: [tool.OcrLine("回退 OCR 得到的正常文字内容。", 0.92)])
        code, _ = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        page = read_json(root / "out" / "garbled.ocr.json")["pages"][0]
        ensure(code == 0, "回退 OCR 成功退出码 0")
        ensure(page["source"] == "ocr", "乱码文字层应改用 OCR")
        ensure("text_layer_suspect_fallback" in page["warnings"], "标记文字层可疑回退")
        ensure("text_layer_text" in page, "保留原始文字层供审计")
        check(True, "乱码文字层回退 OCR 并保留审计信息")

    print("\n== OCR 失败页：不影响其它页，且不能显示为完整成功 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "fail.pdf"
        pdf.write_bytes(build_pdf([None, None, None]))
        engine = FakeEngine(fail_calls={2})
        code, manifest = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory())
        result = read_json(root / "out" / "fail.ocr.json")
        statuses = [page["status"] for page in result["pages"]]
        ensure(code == 1, "存在失败页时退出码 1")
        ensure(statuses == ["ok", "failed", "ok"], f"失败只影响对应页，实际 {statuses}")
        ensure(result["pages"][1]["error"]["code"] == "ocr_failed", "失败页记录错误码")
        ensure(result["status"] == "partial", "文件整体标记 partial")
        ensure(manifest["items"][0]["summary"]["failed_pages"] == 1, "清单汇总失败页数")
        exported = (root / "out" / "fail.ocr.txt").read_text(encoding="utf-8")
        ensure(exported.count("\f") == 2, "失败页仍保留页码位置")
        check(True, "单页 OCR 失败不中断、不伪装成功且页码映射完整")

    print("\n== 旋转页：首轮不可靠时尝试旋转并取最佳 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "rotate.pdf"
        pdf.write_bytes(build_pdf([None]))

        def lines_for(image):
            if image.angle == 270:
                return [tool.OcrLine("旋转 270 度后识别出的完整中文内容。", 0.95)]
            return [tool.OcrLine("短", 0.20)]

        engine = FakeEngine(lines_for)
        code, _ = run_tool([pdf], root / "out", engine=engine, factory=renderer_factory(FakeImage(0)))
        page = read_json(root / "out" / "rotate.ocr.json")["pages"][0]
        ensure(code == 0, "旋转兜底退出码 0")
        ensure(page["rotation_degrees"] == 270, f"选择最佳旋转角度，实际 {page['rotation_degrees']}")
        ensure(len(page["rotation_attempts"]) == 4, "记录旋转尝试（0/90/270/180）")
        ensure("旋转 270 度后识别" in page["text"], "采用旋转后的最佳文本")
        check(True, "旋转页兜底选择最佳结果并记录尝试")

    print("\n== 页码选择与页数上限 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "pages.pdf"
        pdf.write_bytes(build_pdf([LONG_TEXT, None]))
        engine = FakeEngine(lambda image: [tool.OcrLine("第二页扫描内容文字。", 0.9)])
        options = tool.OcrOptions(pages={2})
        run_tool([pdf], root / "out", options=options, engine=engine, factory=renderer_factory())
        result = read_json(root / "out" / "pages.ocr.json")
        ensure(result["pages"][0]["status"] == "skipped", "未选中的页标记 skipped")
        ensure(result["pages"][1]["source"] == "ocr", "选中的页正常 OCR")
        with with_tmp() as temp2:
            root2 = Path(temp2)
            pdf2 = root2 / "limit.pdf"
            pdf2.write_bytes(build_pdf([None, None]))
            limited = tool.OcrOptions(max_pages=1)
            run_tool([pdf2], root2 / "out", options=limited, engine=FakeEngine(), factory=renderer_factory())
            result2 = read_json(root2 / "out" / "limit.ocr.json")
            ensure(result2["pages"][1]["status"] == "skipped", "超过页数上限的页 skipped")
            ensure("page_limit_reached" in result2["pages"][1]["warnings"], "标记页数上限")
        check(True, "页码选择与页数上限行为正确")

    print("\n== 输出保护与原件不变 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "keep.pdf"
        payload = build_pdf([LONG_TEXT])
        pdf.write_bytes(payload)
        before = tool.sha256_file(pdf)
        code, _ = run_tool([pdf], root / "out", engine=FakeEngine(), factory=renderer_factory())
        ensure(code == 0, "首次运行成功")
        ensure(tool.sha256_file(pdf) == before, "原件 SHA-256 不变")
        code, manifest = run_tool([pdf], root / "out", engine=FakeEngine(), factory=renderer_factory())
        ensure(code == 1, "已有输出且未 --overwrite 时拒绝覆盖")
        ensure(manifest["items"][0]["error"]["code"] == "output_exists", "记录 output_exists")
        original_json = read_json(root / "out" / "keep.ocr.json")
        ensure(original_json["source"]["sha256"] == before, "旧输出保持原样")
        code, _ = run_tool([pdf], root / "out", engine=FakeEngine(), factory=renderer_factory(), overwrite=True)
        ensure(code == 0, "--overwrite 允许重跑")
        check(True, "拒绝覆盖保护生效且原件从不被修改")

    print("\n== 运行清单覆盖语义：未 --overwrite 时不得改写旧清单 ==")
    with with_tmp() as temp:
        root = Path(temp)
        pdf = root / "keep.pdf"
        pdf.write_bytes(build_pdf([LONG_TEXT]))
        out = root / "out"
        code, _ = run_tool([pdf], out, engine=FakeEngine(), factory=renderer_factory())
        ensure(code == 0, "首次运行成功")
        first_hashes = {
            name: tool.sha256_file(out / name)
            for name in ("keep.ocr.json", "keep.ocr.txt", "ocr_run.json")
        }
        code, _ = run_tool([pdf], out, engine=FakeEngine(), factory=renderer_factory())
        ensure(code == 1, "同输入无 --overwrite 重跑被拒绝")
        after_hashes = {
            name: tool.sha256_file(out / name)
            for name in ("keep.ocr.json", "keep.ocr.txt", "ocr_run.json")
        }
        ensure(first_hashes == after_hashes, "旧输出与旧 ocr_run.json 完全未被改动")
        manifests = sorted(path.name for path in out.glob("ocr_run*.json") if path.is_file())
        ensure("ocr_run.json" in manifests and len(manifests) >= 2, "新运行写入了独立清单")
        new_name = next(name for name in manifests if name != "ocr_run.json")
        new_manifest = read_json(out / new_name)
        ensure(new_manifest["exit_code"] == 1
               and new_manifest["items"][0]["error"]["code"] == "output_exists",
               "新清单如实记录本次失败，不覆盖旧成功清单")
        ensure(new_manifest.get("manifest") == new_name, "清单记录自身文件名")

        other = root / "other.pdf"
        other.write_bytes(build_pdf([LONG_TEXT]))
        code, _ = run_tool([other], out, engine=FakeEngine(), factory=renderer_factory())
        ensure(code == 0, "同目录处理新一批输入无需覆盖成功")
        ensure(tool.sha256_file(out / "ocr_run.json") == first_hashes["ocr_run.json"],
               "新批次仍不改写旧 ocr_run.json")
        ensure(len(list(out.glob("ocr_run*.json"))) >= 3, "每批新输入各自保留清单")

        code, _ = run_tool([pdf], out, engine=FakeEngine(), factory=renderer_factory(), overwrite=True)
        ensure(code == 0, "--overwrite 时允许覆盖")
        ensure(read_json(out / "ocr_run.json")["exit_code"] == 0,
               "--overwrite 覆盖固定清单且记录本次成功")
        check(True, "清单与输出采用一致的覆盖语义，旧证据默认保留")

    print("\n== 其它输入边界：损坏 PDF / 图片 / 不支持格式 / 同名输出 ==")
    with with_tmp() as temp:
        root = Path(temp)
        bad = root / "bad.pdf"
        bad.write_bytes(b"this is not a pdf")
        code, manifest = run_tool([bad], root / "out", engine=FakeEngine(), factory=renderer_factory())
        result = read_json(root / "out" / "bad.ocr.json")
        ensure(code == 1, "损坏 PDF 退出码 1")
        ensure(result["status"] == "failed" and result["error"]["code"] == "pdf_open_failed", "记录 pdf_open_failed")

        image = root / "scan.png"
        image.write_bytes(b"fake-image-bytes")
        engine = FakeEngine(lambda value: [tool.OcrLine("图片输入识别内容文字。", 0.9)])
        code, _ = run_tool([image], root / "img-out", engine=engine, factory=renderer_factory())
        result = read_json(root / "img-out" / "scan.ocr.json")
        ensure(code == 0 and result["source"]["kind"] == "image", "图片输入按单页处理")
        ensure(result["pages"][0]["source"] == "ocr", "图片输入走 OCR")

        unsupported = root / "notes.xyz"
        unsupported.write_text("x", encoding="utf-8")
        code, manifest = run_tool([unsupported], root / "out2", engine=FakeEngine())
        ensure(code == 1 and manifest["items"][0]["error"]["code"] == "unsupported_input", "不支持格式明确报错")

        first_dir = root / "a"
        second_dir = root / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "same.pdf"
        second = second_dir / "same.pdf"
        first.write_bytes(build_pdf([LONG_TEXT]))
        second.write_bytes(build_pdf([None]))
        engine = FakeEngine()
        code, manifest = run_tool([first, second], root / "dup-out", engine=engine, factory=renderer_factory())
        names = {Path(item["output"]).name for item in manifest["items"]}
        ensure(code == 0 and len(names) == 2, "同名输入输出不冲突")
        check(True, "损坏 PDF/图片/不支持格式/同名输出边界处理正确")

    print("\n== CLI：--engine none 的明确失败与退出码 ==")
    with with_tmp() as temp:
        root = Path(temp)
        text_pdf = root / "text.pdf"
        text_pdf.write_bytes(build_pdf([LONG_TEXT]))
        blank_pdf = root / "blank.pdf"
        blank_pdf.write_bytes(build_pdf([None]))
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        code = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "ocr_preprocess.py"),
             str(text_pdf), "--out", str(root / "out"), "--engine", "none"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        ensure(code.returncode == 0, f"纯文字层 PDF 用 --engine none 应成功：{code.stderr[:300]}")
        result = read_json(root / "out" / "text.ocr.json")
        ensure(result["pages"][0]["source"] == "text_layer", "CLI 下文字层保留")

        blank = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "ocr_preprocess.py"),
             str(blank_pdf), "--out", str(root / "out2"), "--engine", "none"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        ensure(blank.returncode == 1, "需要 OCR 但引擎关闭时退出码 1")
        result = read_json(root / "out2" / "blank.ocr.json")
        ensure(result["pages"][0]["error"]["code"] == "ocr_engine_unavailable", "记录 ocr_engine_unavailable")
        ensure((root / "out2" / "ocr_run.json").exists(), "写出运行清单 ocr_run.json")
        check(True, "CLI --engine none 对扫描页明确失败并写出清单")

    print(f"\n结果: {len(PASS)} 通过, {len(FAIL)} 失败")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
