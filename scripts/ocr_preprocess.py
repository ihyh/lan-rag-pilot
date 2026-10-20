"""OCR 预处理小工具：独立运行，不修改原件，不接入默认安装与索引。

对应交接文档「第二轮：OCR 预处理小工具」。本工具的 OCR 依赖（rapidocr /
onnxruntime / pypdfium2 / Pillow）写在自己的 requirements-ocr.txt 中，不属于
默认安装；应用服务不会导入本文件。

核心行为：
- 按页判断是否需要 OCR（PDF 逐页看文字层，不按整份文件总字数）；
- 可用文字层原样保留，混合 PDF 只对有需要的页做 OCR；
- 原件只读、输出另存；结果含页码映射、来源摘要、引擎/版本、每页状态；
- 区分原始文字与 OCR 文字；引擎没有可靠置信度时不伪造；
- 失败页/疑似乱码页明确标记，不能显示为无警告的完整成功；
- 不重建现有索引、不覆盖私有文档。

用法（Windows PowerShell）：
    py -3.12 -m venv .venv-ocr
    .venv-ocr\\Scripts\\python.exe -m pip install -r requirements-ocr.txt
    .venv-ocr\\Scripts\\python.exe scripts\\ocr_preprocess.py 扫描件.pdf --out out\\ocr
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol, Sequence

TOOL_NAME = "ocr_preprocess"
TOOL_VERSION = "0.1.0"
RESULT_SUFFIX = ".ocr.json"
TEXT_SUFFIX = ".ocr.txt"
MANIFEST_NAME = "ocr_run.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_SUFFIXES = {".pdf"}
DEFAULT_MIN_TEXT_CHARS = 40
DEFAULT_DPI = 200
DEFAULT_LINE_SCORE = 0.60
DEFAULT_PAGE_SCORE = 0.60
DEFAULT_ROTATE_SCORE = 0.75
DEFAULT_MAX_PAGES = 500
MIN_OCR_TEXT_CHARS = 10
GARBLE_RATIO_LIMIT = 0.02


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class OcrLine:
    """一行的 OCR 结果；score 为 None 表示引擎没有给出可靠置信度。"""

    text: str
    score: float | None = None


@dataclass
class OcrOptions:
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS
    dpi: int = DEFAULT_DPI
    line_score: float = DEFAULT_LINE_SCORE
    page_score: float = DEFAULT_PAGE_SCORE
    rotate_score: float = DEFAULT_ROTATE_SCORE
    auto_rotate: bool = True
    max_pages: int = DEFAULT_MAX_PAGES
    pages: set[int] | None = None
    include_text_layer_audit: bool = True


class OcrEngineUnavailable(RuntimeError):
    """OCR 引擎不可用（依赖未安装等）。"""


class Engine(Protocol):
    name: str

    def info(self) -> dict:
        ...

    def recognize(self, image) -> list[OcrLine]:
        ...


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def count_chars(text: str) -> int:
    return len(re.findall(r"\S", text or ""))


def _garble_ratio(text: str) -> float:
    if not text:
        return 1.0
    bad = 0
    for char in text:
        category = unicodedata.category(char)
        if char == "\ufffd":
            bad += 1
        elif category == "Cc" and char not in "\n\t\r":
            bad += 1
        elif category == "Co":
            bad += 1
    return bad / max(1, len(text))


def classify_text_layer(text: str, min_chars: int) -> str:
    """返回 usable / insufficient / suspect；必须按页调用。"""
    chars = count_chars(text)
    if chars == 0:
        return "insufficient"
    if _garble_ratio(text) > GARBLE_RATIO_LIMIT:
        return "suspect"
    if chars < min_chars:
        return "insufficient"
    return "usable"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_stem(path: Path) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", path.stem).strip(" .")
    return stem or "document"


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"无法序列化 {type(value)!r}")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - 元数据异常不应中断输出
        return None


# ---------------------------------------------------------------------------
# 引擎：RapidOCR（CPU onnxruntime；模型随包分发，无需另外下载）
# ---------------------------------------------------------------------------


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self) -> None:
        try:
            rapidocr = importlib.import_module("rapidocr")
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的明确提示
            raise OcrEngineUnavailable(
                "未安装 rapidocr。请使用独立环境安装 requirements-ocr.txt："
                "python -m pip install -r requirements-ocr.txt"
            ) from exc
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        self._engine = rapidocr.RapidOCR()
        self._module_file = Path(getattr(rapidocr, "__file__", "")) if getattr(rapidocr, "__file__", None) else None

    def info(self) -> dict:
        models = []
        if self._module_file is not None:
            models_dir = self._module_file.parent / "models"
            if models_dir.is_dir():
                for path in sorted(models_dir.glob("*.onnx")):
                    models.append({"name": path.name, "size_bytes": path.stat().st_size})
        return {
            "name": self.name,
            "package": "rapidocr",
            "package_version": _package_version("rapidocr"),
            "runtime": "onnxruntime",
            "runtime_version": _package_version("onnxruntime"),
            "models": models,
            "notes": "模型随 rapidocr 包分发，CPU 推理；首次运行不需要联网下载。",
        }

    def recognize(self, image) -> list[OcrLine]:
        result = self._engine(image)
        if result is None:
            return []
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        lines: list[OcrLine] = []
        for index, text in enumerate(texts):
            score = None
            if index < len(scores) and scores[index] is not None:
                try:
                    score = float(scores[index])
                except (TypeError, ValueError):
                    score = None
            lines.append(OcrLine(str(text), score))
        return lines


def build_engine(name: str) -> Engine | None:
    if name == "none":
        return None
    if name == "rapidocr":
        return RapidOcrEngine()
    raise OcrEngineUnavailable(f"未知 OCR 引擎：{name}")


# ---------------------------------------------------------------------------
# 渲染：PDF 逐页转图（pypdfium2）与图片输入（Pillow）
# ---------------------------------------------------------------------------


class PdfPageRenderer:
    def __init__(self, path: Path, dpi: int) -> None:
        try:
            pdfium = importlib.import_module("pypdfium2")
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise OcrEngineUnavailable(
                "未安装 pypdfium2，无法把扫描 PDF 页面渲染成图片。"
                "请安装 requirements-ocr.txt。"
            ) from exc
        self._document = pdfium.PdfDocument(str(path))
        self._scale = dpi / 72.0

    def render(self, index: int):
        page = self._document[index]
        try:
            bitmap = page.render(scale=self._scale)
            try:
                return bitmap.to_pil()
            finally:
                bitmap.close()
        finally:
            page.close()

    def close(self) -> None:
        self._document.close()


class ImageFileRenderer:
    """图片输入：单帧图片或多帧 TIFF；按帧号读取。"""

    def __init__(self, path: Path) -> None:
        try:
            image_module = importlib.import_module("PIL.Image")
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise OcrEngineUnavailable(
                "未安装 Pillow，无法读取图片输入。请安装 requirements-ocr.txt。"
            ) from exc
        self._path = path
        self._image_module = image_module
        # 多帧 TIFF 必须逐帧处理，不能静默只读第一页。
        with self._image_module.open(self._path) as image:
            self.page_count = int(getattr(image, "n_frames", 1) or 1)

    def render(self, index: int):
        with self._image_module.open(self._path) as image:
            if index:
                image.seek(index)
            return image.convert("RGB").copy()

    def close(self) -> None:
        return None


class _FakeImage:
    """测试用图像：记录当前旋转角度，便于断言旋转兜底。"""

    def __init__(self, angle: int = 0) -> None:
        self.angle = angle

    def rotate(self, angle: int, expand: bool = True):
        return _FakeImage((self.angle + angle) % 360)


def _rotate_image(image, angle: int):
    if angle % 360 == 0:
        return image
    rotate = getattr(image, "rotate", None)
    if rotate is None:
        return image
    return rotate(angle, expand=True)


# ---------------------------------------------------------------------------
# 页面级处理
# ---------------------------------------------------------------------------


def _lines_text(lines: Sequence[OcrLine]) -> str:
    return "\n".join(line.text for line in lines if line.text is not None)


def _attempt_rank(lines: Sequence[OcrLine]) -> tuple[float, int]:
    scores = [line.score for line in lines if line.score is not None]
    mean = sum(scores) / len(scores) if scores else 0.0
    return (mean, count_chars(_lines_text(lines)))


def _mean_score(lines: Sequence[OcrLine]) -> float | None:
    scores = _line_scores(lines)
    if scores is None:
        return None
    return round(sum(scores) / len(scores), 4)


def _line_scores(lines: Sequence[OcrLine]) -> list[float] | None:
    scores = [line.score for line in lines]
    if not scores or any(score is None for score in scores):
        return None
    return [float(score) for score in scores]


def recognize_with_rotation(image, engine: Engine, options: OcrOptions) -> dict:
    """对一页做 OCR；首轮结果不可靠时尝试 90/180/270 度旋转，取最佳一轮。"""
    attempts: list[dict] = []
    first = engine.recognize(image)
    attempts.append({
        "rotation": 0,
        "lines": len(first),
        "chars": count_chars(_lines_text(first)),
        "mean_score": _mean_score(first),
    })
    best = (0, first, _attempt_rank(first))
    weak = count_chars(_lines_text(first)) < MIN_OCR_TEXT_CHARS
    scores = _line_scores(first)
    if scores is not None and sum(scores) / len(scores) < options.rotate_score:
        weak = True
    if options.auto_rotate and weak:
        for angle in (90, 270, 180):
            rotated = _rotate_image(image, angle)
            lines = engine.recognize(rotated)
            attempts.append({
                "rotation": angle,
                "lines": len(lines),
                "chars": count_chars(_lines_text(lines)),
                "mean_score": _mean_score(lines),
            })
            rank = _attempt_rank(lines)
            if rank > best[2]:
                best = (angle, lines, rank)
    return {"rotation": best[0], "lines": best[1], "attempts": attempts}


def _page_confidence(lines: Sequence[OcrLine]) -> dict | None:
    scores = _line_scores(lines)
    if scores is None:
        return None
    return {
        "mean": round(sum(scores) / len(scores), 4),
        "min": round(min(scores), 4),
        "lines": len(scores),
    }


def _ocr_page_record(page_no: int, image, engine: Engine, options: OcrOptions,
                     base: dict) -> dict:
    outcome = recognize_with_rotation(image, engine, options)
    lines: list[OcrLine] = outcome["lines"]
    text = _lines_text(lines)
    chars = count_chars(text)
    record = dict(base)
    record.update(
        source="ocr",
        text=text,
        chars=chars,
        rotation_degrees=outcome["rotation"],
        rotation_attempts=outcome["attempts"],
    )
    if outcome["rotation"] != 0:
        record["warnings"].append("rotated_to_read")
    confidence = _page_confidence(lines)
    if confidence is not None:
        record["confidence"] = confidence
    uncertain = []
    if confidence is not None:
        for index, line in enumerate(lines, start=1):
            if line.score is not None and line.score < options.line_score:
                uncertain.append({
                    "line": index,
                    "score": round(float(line.score), 4),
                    "text": line.text,
                })
                if len(uncertain) >= 50:
                    break
    if uncertain:
        record["uncertain_lines"] = uncertain
        # 页面均分可能很高（例如 9 行 0.99 + 1 行关键数字 0.55），不能只把低分行
        # 藏在 uncertain_lines 里而让整页显示 ok；有低分行就必须标记可疑。
        record["warnings"].append("low_confidence_line")
    if not lines or chars == 0:
        record["warnings"].append("no_text_detected")
    elif chars < MIN_OCR_TEXT_CHARS:
        record["warnings"].append("short_text")
    if _garble_ratio(text) > GARBLE_RATIO_LIMIT:
        record["warnings"].append("garbled_text")
    if confidence is not None and confidence["mean"] < options.page_score:
        record["warnings"].append("low_confidence")
    if record["warnings"]:
        record["status"] = "suspect"
    return record


def _text_layer_record(page_no: int, text: str, base: dict) -> dict:
    record = dict(base)
    record.update(source="text_layer", text=text, chars=count_chars(text), status="ok")
    return record


# ---------------------------------------------------------------------------
# 文档级处理
# ---------------------------------------------------------------------------


def _new_page_record(page_no: int) -> dict:
    return {"page": page_no, "source": None, "status": "ok", "text": "", "chars": 0, "warnings": []}


def _summary(pages: Sequence[dict]) -> dict:
    def count(predicate) -> int:
        return sum(1 for page in pages if predicate(page))

    return {
        "pages": len(pages),
        "text_layer_pages": count(lambda p: p["source"] == "text_layer"),
        "ocr_pages": count(lambda p: p["source"] == "ocr"),
        "skipped_pages": count(lambda p: p["status"] == "skipped"),
        "ok_pages": count(lambda p: p["status"] == "ok"),
        "suspect_pages": count(lambda p: p["status"] == "suspect"),
        "failed_pages": count(lambda p: p["status"] == "failed"),
    }


def _page_selection(total: int, options: OcrOptions) -> set[int] | None:
    if options.pages is None:
        return None
    return {page for page in options.pages if 1 <= page <= total}


def process_document(
    path: Path,
    options: OcrOptions,
    *,
    engine: Engine | None,
    renderer_factory: Callable[[Path, OcrOptions], object] | None = None,
) -> dict:
    """处理一个输入文件，返回结果字典（也用于写 .ocr.json）。"""
    path = Path(path)
    source: dict = {
        "path": str(path.resolve()),
        "filename": path.name,
        "suffix": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "kind": "pdf" if path.suffix.lower() in PDF_SUFFIXES else "image",
    }
    result: dict = {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "created_at": _now_iso(),
        "engine": engine.info() if engine is not None else None,
        "options": {
            "min_text_chars": options.min_text_chars,
            "dpi": options.dpi,
            "line_score": options.line_score,
            "page_score": options.page_score,
            "rotate_score": options.rotate_score,
            "auto_rotate": options.auto_rotate,
            "max_pages": options.max_pages,
            "pages": sorted(options.pages) if options.pages is not None else None,
        },
        "source": source,
        "pages": [],
        "warnings": [],
        "status": "ok",
    }
    renderer = None
    try:
        if source["kind"] == "pdf":
            try:
                pypdf = importlib.import_module("pypdf")
                reader = pypdf.PdfReader(str(path))
            except Exception as exc:  # noqa: BLE001 - 损坏 PDF 也要落盘失败结果
                result["status"] = "failed"
                result["error"] = {"code": "pdf_open_failed", "message": str(exc)[:300]}
                return result
            total_pages = len(reader.pages)
            result["source"]["pages"] = total_pages
            selected = _page_selection(total_pages, options)
            limit = min(total_pages, options.max_pages)
            if total_pages > options.max_pages:
                result["warnings"].append("max_pages_limit_reached")
            if renderer_factory is not None:
                renderer_getter = lambda: renderer_factory(path, options)  # noqa: E731
            else:
                renderer_getter = lambda: PdfPageRenderer(path, options.dpi)  # noqa: E731
            for page_index in range(total_pages):
                page_no = page_index + 1
                record = _new_page_record(page_no)
                if selected is not None and page_no not in selected:
                    record["status"] = "skipped"
                    record["warnings"].append("not_selected")
                    result["pages"].append(record)
                    continue
                if page_no > limit:
                    record["status"] = "skipped"
                    record["warnings"].append("page_limit_reached")
                    result["pages"].append(record)
                    continue
                try:
                    layer_text = reader.pages[page_index].extract_text() or ""
                except Exception as exc:  # noqa: BLE001
                    layer_text = ""
                    record["warnings"].append("text_layer_error")
                quality = classify_text_layer(layer_text, options.min_text_chars)
                if quality == "usable":
                    result["pages"].append(_text_layer_record(page_no, layer_text, record))
                    continue
                if quality == "suspect":
                    record["warnings"].append("text_layer_suspect_fallback")
                    if options.include_text_layer_audit:
                        record["text_layer_text"] = layer_text
                if engine is None:
                    record.update(source="none", status="failed")
                    record["error"] = {
                        "code": "ocr_engine_unavailable",
                        "message": "该页需要 OCR，但本次以 --engine none 运行或引擎不可用。",
                    }
                    record["warnings"].append("ocr_unavailable")
                    result["pages"].append(record)
                    continue
                try:
                    if renderer is None:
                        renderer = renderer_getter()
                    image = renderer.render(page_index)
                except Exception as exc:  # noqa: BLE001
                    record.update(source="none", status="failed")
                    record["error"] = {"code": "render_failed", "message": str(exc)[:300]}
                    result["pages"].append(record)
                    continue
                try:
                    result["pages"].append(_ocr_page_record(page_no, image, engine, options, record))
                except Exception as exc:  # noqa: BLE001
                    record.update(source="ocr", status="failed")
                    record["error"] = {"code": "ocr_failed", "message": str(exc)[:300]}
                    result["pages"].append(record)
        else:
            # 图片输入：单帧图片或多帧 TIFF，必须逐帧处理，不能静默只取第一页。
            if renderer_factory is not None:
                try:
                    renderer = renderer_factory(path, options)
                except Exception as exc:  # noqa: BLE001
                    result["status"] = "failed"
                    result["source"]["pages"] = 1
                    result["error"] = {"code": "image_open_failed", "message": str(exc)[:300]}
                    return result
                total_pages = int(getattr(renderer, "page_count", 1) or 1)
            else:
                try:
                    renderer = ImageFileRenderer(path)
                except OcrEngineUnavailable as exc:
                    renderer = None
                    total_pages = 1
                    if engine is not None:
                        result["status"] = "failed"
                        result["source"]["pages"] = 1
                        result["error"] = {"code": "image_open_failed", "message": str(exc)[:300]}
                        return result
                else:
                    total_pages = int(getattr(renderer, "page_count", 1) or 1)
            result["source"]["pages"] = total_pages
            selected = _page_selection(total_pages, options)
            limit = min(total_pages, options.max_pages)
            if total_pages > options.max_pages:
                result["warnings"].append("max_pages_limit_reached")
            for page_index in range(total_pages):
                page_no = page_index + 1
                record = _new_page_record(page_no)
                if selected is not None and page_no not in selected:
                    record["status"] = "skipped"
                    record["warnings"].append("not_selected")
                    result["pages"].append(record)
                    continue
                if page_no > limit:
                    record["status"] = "skipped"
                    record["warnings"].append("page_limit_reached")
                    result["pages"].append(record)
                    continue
                if engine is None:
                    record.update(source="none", status="failed")
                    record["error"] = {
                        "code": "ocr_engine_unavailable",
                        "message": "图片输入需要 OCR，但本次以 --engine none 运行或引擎不可用。",
                    }
                    record["warnings"].append("ocr_unavailable")
                    result["pages"].append(record)
                    continue
                try:
                    image = renderer.render(page_index)
                except Exception as exc:  # noqa: BLE001
                    record.update(source="none", status="failed")
                    record["error"] = {"code": "render_failed", "message": str(exc)[:300]}
                    result["pages"].append(record)
                    continue
                try:
                    result["pages"].append(_ocr_page_record(page_no, image, engine, options, record))
                except Exception as exc:  # noqa: BLE001
                    record.update(source="ocr", status="failed")
                    record["error"] = {"code": "ocr_failed", "message": str(exc)[:300]}
                    result["pages"].append(record)
    finally:
        if renderer is not None:
            close = getattr(renderer, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    after_sha = sha256_file(path)
    if after_sha != source["sha256"]:
        result["status"] = "failed"
        result["error"] = {
            "code": "source_modified_detected",
            "message": "处理前后原件 SHA-256 不一致：请立即核对，本工具不应修改原件。",
        }
    elif any(page["status"] == "failed" for page in result["pages"]):
        result["status"] = "partial" if any(
            page["status"] != "failed" for page in result["pages"]
        ) else "failed"
    elif any(page["status"] == "suspect" for page in result["pages"]):
        result["status"] = "partial"
    result["summary"] = _summary(result["pages"])
    return result


# ---------------------------------------------------------------------------
# 运行与输出
# ---------------------------------------------------------------------------


def _text_export(result: dict) -> str:
    pages = result.get("pages") or []
    return "\f".join(page.get("text") or "" for page in pages)


def _unique_stem(path: Path, used: set[str], sha256: str) -> str:
    stem = _safe_stem(path)
    if stem not in used:
        used.add(stem)
        return stem
    candidate = f"{stem}-{sha256[:8]}"
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{stem}-{sha256[:8]}-{suffix}"
    used.add(candidate)
    return candidate


def _manifest_output_path(out_dir: Path, overwrite: bool, created_at: str) -> Path:
    """运行清单写入口径：未显式 --overwrite 时绝不覆盖已有清单。

    - 目录里还没有 `ocr_run.json`：沿用固定名；
    - 已有清单且未 --overwrite：生成唯一的 `ocr_run-<时间戳>[-N].json`，
      保留先前批次的成功/失败证据，不用新清单覆盖旧清单；
    - --overwrite：允许覆盖固定名清单（与输出文件语义一致）。
    """
    path = out_dir / MANIFEST_NAME
    if overwrite or not path.exists():
        return path
    stamp = re.sub(r"\D", "", created_at)[:14] or "run"
    candidate = out_dir / f"ocr_run-{stamp}.json"
    index = 1
    while candidate.exists():
        index += 1
        candidate = out_dir / f"ocr_run-{stamp}-{index}.json"
    return candidate


def run(
    inputs: Sequence[Path],
    out_dir: Path,
    options: OcrOptions,
    *,
    engine: Engine | None = None,
    renderer_factory: Callable[[Path, OcrOptions], object] | None = None,
    overwrite: bool = False,
    write_manifest: bool = True,
) -> tuple[int, dict]:
    """处理多个输入并写出结果；返回 (退出码, 清单)。退出码 0 成功、1 有失败页/文件。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    created_at = _now_iso()
    used_stems: set[str] = set()
    items: list[dict] = []
    exit_code = 0
    for raw_path in inputs:
        path = Path(raw_path)
        item = {"source": str(path.resolve()), "status": "failed"}
        if not path.is_file():
            item["error"] = {"code": "input_missing", "message": "输入文件不存在或不是文件。"}
            items.append(item)
            exit_code = 1
            continue
        if path.suffix.lower() not in PDF_SUFFIXES | IMAGE_SUFFIXES:
            item["error"] = {
                "code": "unsupported_input",
                "message": "只支持 PDF 与常见图片格式（PNG/JPG/BMP/TIFF/WebP）。",
            }
            items.append(item)
            exit_code = 1
            continue
        sha = sha256_file(path)
        stem = _unique_stem(path, used_stems, sha)
        json_path = out_dir / f"{stem}{RESULT_SUFFIX}"
        text_path = out_dir / f"{stem}{TEXT_SUFFIX}"
        item.update(
            output=str(json_path.resolve()),
            text_output=str(text_path.resolve()),
            sha256=sha,
        )
        if (json_path.exists() or text_path.exists()) and not overwrite:
            item["status"] = "failed"
            item["error"] = {
                "code": "output_exists",
                "message": "输出已存在；确认要覆盖请加 --overwrite（原件仍不会被修改）。",
            }
            items.append(item)
            exit_code = 1
            continue
        try:
            result = process_document(
                path, options, engine=engine, renderer_factory=renderer_factory
            )
        except Exception as exc:  # noqa: BLE001 - 单文件失败不影响其它文件
            item["status"] = "failed"
            item["error"] = {
                "code": "document_failed",
                "message": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
            items.append(item)
            exit_code = 1
            continue
        _write_json(json_path, result)
        text_path.write_text(_text_export(result), encoding="utf-8")
        item["status"] = result["status"]
        item["summary"] = result.get("summary")
        if result["status"] == "failed" or result.get("summary", {}).get("failed_pages"):
            exit_code = 1
        items.append(item)
    manifest = {
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "created_at": created_at,
        "engine": engine.info() if engine is not None else None,
        "options": {
            "min_text_chars": options.min_text_chars,
            "dpi": options.dpi,
            "line_score": options.line_score,
            "page_score": options.page_score,
            "rotate_score": options.rotate_score,
            "auto_rotate": options.auto_rotate,
            "max_pages": options.max_pages,
            "pages": sorted(options.pages) if options.pages is not None else None,
            "overwrite": overwrite,
        },
        "items": items,
        "exit_code": exit_code,
    }
    if write_manifest:
        manifest_path = _manifest_output_path(out_dir, overwrite, created_at)
        manifest["manifest"] = manifest_path.name
        _write_json(manifest_path, manifest)
    return exit_code, manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_page_selection(spec: str, total: int | None = None) -> set[int]:
    pages: set[int] = set()
    for part in re.split(r"[,，]", spec.strip()):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*[-~]\s*(\d+)", part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start < 1 or end < start:
                raise ValueError(f"页码范围无效：{part}")
            pages.update(range(start, end + 1))
        elif re.fullmatch(r"\d+", part):
            page = int(part)
            if page < 1:
                raise ValueError(f"页码必须从 1 开始：{part}")
            pages.add(page)
        else:
            raise ValueError(f"无法解析页码：{part}")
    if not pages:
        raise ValueError("页码选择为空")
    return pages


def _human_summary(manifest: dict) -> str:
    lines = []
    for item in manifest["items"]:
        summary = item.get("summary") or {}
        lines.append(
            f"[{item['status']}] {Path(item['source']).name}: "
            f"{summary.get('pages', 0)} 页，文字层 {summary.get('text_layer_pages', 0)}，"
            f"OCR {summary.get('ocr_pages', 0)}，可疑 {summary.get('suspect_pages', 0)}，"
            f"失败 {summary.get('failed_pages', 0)}"
        )
        if item.get("error"):
            lines.append(f"  ! {item['error']['code']}: {item['error']['message']}")
        if item.get("output"):
            lines.append(f"  -> {item['output']}")
    engine_info = manifest.get("engine") or {}
    if engine_info:
        lines.append(
            f"引擎：{engine_info.get('name')} {engine_info.get('package_version')} "
            f"({engine_info.get('runtime')} {engine_info.get('runtime_version')})"
        )
    else:
        lines.append("引擎：未启用（--engine none，仅保留可用文字层）")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr_preprocess",
        description="按页判断的 OCR 预处理工具：保留可用文字层，只 OCR 需要的页；不修改原件。",
    )
    parser.add_argument("inputs", nargs="+", help="PDF 或图片文件")
    parser.add_argument("--out", required=True, help="输出目录（必须是单独目录，会新建）")
    parser.add_argument("--engine", default="rapidocr", choices=("rapidocr", "none"),
                        help="OCR 引擎；none 表示只保留可用文字层（默认 rapidocr）")
    parser.add_argument("--min-text-chars", type=int, default=DEFAULT_MIN_TEXT_CHARS,
                        help=f"文字层可用阈值（非空白字符数，默认 {DEFAULT_MIN_TEXT_CHARS}）")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                        help=f"扫描页渲染 DPI（默认 {DEFAULT_DPI}）")
    parser.add_argument("--line-score", type=float, default=DEFAULT_LINE_SCORE,
                        help=f"低于该行置信度的行列入 uncertain_lines（默认 {DEFAULT_LINE_SCORE}）")
    parser.add_argument("--page-score", type=float, default=DEFAULT_PAGE_SCORE,
                        help=f"页均置信度低于该值标记 low_confidence（默认 {DEFAULT_PAGE_SCORE}）")
    parser.add_argument("--rotate-score", type=float, default=DEFAULT_ROTATE_SCORE,
                        help=f"首轮均分低于该值时尝试旋转（默认 {DEFAULT_ROTATE_SCORE}）")
    parser.add_argument("--no-auto-rotate", action="store_true", help="关闭 90/180/270 度旋转兜底")
    parser.add_argument("--pages", default="", help="只处理这些页，如 1-3,5（不选中的页记为 skipped）")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"单文件最多处理页数（默认 {DEFAULT_MAX_PAGES}）")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖同名输出（原件仍只读）")
    parser.add_argument("--json", action="store_true", help="向 stdout 输出完整清单 JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.dpi < 36 or args.dpi > 600:
        parser.error("--dpi 需在 36-600 之间")
    if args.min_text_chars < 0:
        parser.error("--min-text-chars 不能为负")
    if args.max_pages < 1:
        parser.error("--max-pages 至少为 1")
    try:
        pages = parse_page_selection(args.pages) if args.pages else None
    except ValueError as exc:
        parser.error(str(exc))
    inputs = [Path(item) for item in args.inputs]
    out_dir = Path(args.out)
    for path in inputs:
        if path.resolve() == out_dir.resolve():
            parser.error("--out 不能与输入文件相同")
    options = OcrOptions(
        min_text_chars=args.min_text_chars,
        dpi=args.dpi,
        line_score=args.line_score,
        page_score=args.page_score,
        rotate_score=args.rotate_score,
        auto_rotate=not args.no_auto_rotate,
        max_pages=args.max_pages,
        pages=pages,
    )
    try:
        engine = build_engine(args.engine)
    except OcrEngineUnavailable as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    exit_code, manifest = run(
        inputs, out_dir, options, engine=engine, overwrite=args.overwrite
    )
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default))
    else:
        print(_human_summary(manifest))
    if exit_code != 0:
        print("存在失败页或失败文件：请查看输出 JSON 与 --json 清单；失败页不能当作完整成功。",
              file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
