"""文档解析：PDF / DOC / DOCX / XLSX / TXT / MD -> (页码|段落, 文本) 单元列表。

- PDF 保留页码；扫描件/无文本层直接报错（本试点不含 OCR）。
- DOC/DOCX/TXT/MD 以“段落号”作为位置引用；XLSX 以工作表行作为文本单元。
"""
from __future__ import annotations

import io
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import settings

EXTENSIONS = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".txt": "txt",
    ".md": "md",
}

# 各类型的允许 MIME；application/octet-stream 一律放行，最终以魔数+实际解析为准
MIME_MAP: dict[str, set[str]] = {
    "pdf": {"application/pdf", "application/x-pdf"},
    "doc": {"application/msword", "application/vnd.ms-word"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "txt": {"text/plain"},
    "md": {"text/markdown", "text/x-markdown", "text/plain"},
}
_ALLOWED_MIMES: set[str] = set().union(*MIME_MAP.values()) | {"application/octet-stream"}


class ParseError(Exception):
    def __init__(self, message: str, code: str = "parse_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Unit:
    """一段可独立切块的文本；page/paragraph 至少一个用于来源引用。"""

    text: str
    page: int | None = None          # 1 起始页码（仅 PDF）
    paragraph: int | None = None     # 1 起始段落号（DOC/DOCX/TXT/MD）


def detect_ext(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        raise ParseError(
            f"不支持的文件类型 {ext or '(无扩展名)'}：仅支持 PDF / DOC / DOCX / XLSX / TXT / MD",
            code="unsupported_ext",
        )
    return ext


def check_mime(content_type: str) -> None:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in _ALLOWED_MIMES:
        raise ParseError(f"MIME 类型 {ctype!r} 不在允许范围，疑似伪造扩展名", code="bad_mime")


# ---------------- 解析资源上限（防压缩炸弹 / 超大表格） ----------------
#
# 上传体积上限（默认 25MB）挡不住"解压后几十 GB"的构造文件：OOXML 文件就是 ZIP，
# 一个几 MB 的 docx 可以把 word/document.xml 压成几十 GB。这里在交给
# python-docx / openpyxl 解压之前先做压缩包层面的体检。

# 各类型真正会被解析的主部件（用于有界实际读取）
_OOXML_MAIN_PART = {"docx": "word/document.xml", "xlsx": "xl/sharedStrings.xml"}


class ParseBudget:
    """累计单份文件提取出的字符数，超限即抛 ParseError。

    只限制"文本单元个数"不够：极宽的一行也能是单个超长单元，
    因此必须同时限制累计字符数。
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.chars = 0

    def spend(self, text: str) -> None:
        self.chars += len(text)
        if self.chars > settings.parse_max_text_chars:
            raise ParseError(
                f"{self.label} 提取出的文本已超过 {settings.parse_max_text_chars} 字符上限"
                f"（当前 {self.chars}）。请拆分文件，或调大 RAG_PARSE_MAX_TEXT_CHARS。",
                code="text_too_large",
            )


def check_units(units: list[Unit], label: str) -> None:
    if len(units) > settings.parse_max_units:
        raise ParseError(
            f"{label} 解析出 {len(units)} 个文本单元，超过 {settings.parse_max_units} 上限。"
            "请拆分文件，或调大 RAG_PARSE_MAX_UNITS。",
            code="too_many_units",
        )


def _bounded_part_bytes(zf: zipfile.ZipFile, name: str, cap: int) -> int:
    """实际解压读取某个部件，最多读 cap+1 字节后停止。

    不能只信中央目录里声明的 file_size：攻击者可以谎报一个很小的值，
    真正的解压发生在读取时，所以必须实际读一遍并设上限。
    """
    try:
        with zf.open(name) as handle:
            read = 0
            while True:
                chunk = handle.read(1 << 20)
                if not chunk:
                    break
                read += len(chunk)
                if read > cap:
                    return read
            return read
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
        raise ParseError(
            f"压缩包部件 {name} 读取失败（可能已损坏或使用了不支持的压缩方式）：{exc}",
            code="zip_read_error",
        ) from exc


def check_zip_limits(label: str, kind: str, data: bytes) -> list[str]:
    """压缩包层面的资源体检；通过则返回条目名列表。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ParseError(
            f"文件内容不是有效 {label}（ZIP 损坏）：{exc}", code="bad_magic"
        ) from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > settings.parse_max_zip_entries:
            raise ParseError(
                f"{label} 内含 {len(infos)} 个压缩条目，超过 "
                f"{settings.parse_max_zip_entries} 上限。",
                code="zip_too_many_entries",
            )

        declared = sum(info.file_size for info in infos)
        compressed = sum(info.compress_size for info in infos)
        limit = settings.max_uncompressed_bytes

        if declared > limit:
            raise ParseError(
                f"{label} 解压后约 {declared / 1048576:.1f} MB，超过 "
                f"{settings.parse_max_uncompressed_mb} MB 上限"
                "（可调大 RAG_PARSE_MAX_UNCOMPRESSED_MB，但请先确认服务器内存足够）。",
                code="zip_too_large",
            )

        if compressed > 0 and declared / compressed > settings.parse_max_compression_ratio:
            raise ParseError(
                f"{label} 压缩比约 {declared / compressed:.0f}:1，超过 "
                f"{settings.parse_max_compression_ratio}:1，疑似压缩炸弹，已拒绝。",
                code="zip_bomb_ratio",
            )

        names = zf.namelist()
        # 谎报大小的情况：对真正会被解析的主部件做一次有界实际读取。
        main_part = _OOXML_MAIN_PART.get(kind)
        if main_part and main_part in names:
            actual = _bounded_part_bytes(zf, main_part, limit)
            if actual > limit:
                raise ParseError(
                    f"{label} 的 {main_part} 实际解压超过 "
                    f"{settings.parse_max_uncompressed_mb} MB 上限（已停止读取）。",
                    code="zip_too_large",
                )
        return names


def check_magic(kind: str, data: bytes) -> None:
    if kind == "pdf":
        if not data[:5].startswith(b"%PDF-"):
            raise ParseError("文件内容不是有效 PDF（缺少 %PDF- 文件头）", code="bad_magic")
    elif kind == "docx":
        if data[:4] != b"PK\x03\x04":
            raise ParseError("文件内容不是有效 DOCX（缺少 ZIP/OOXML 头）", code="bad_magic")
        names = check_zip_limits("DOCX", kind, data)
        if "word/document.xml" not in names:
            raise ParseError("文件内容不是有效 DOCX（缺少 word/document.xml）", code="bad_magic")
    elif kind == "doc":
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ParseError("文件内容不是有效 DOC（缺少 OLE 复合文档头）", code="bad_magic")
    elif kind == "xlsx":
        if data[:4] != b"PK\x03\x04":
            raise ParseError("文件内容不是有效 XLSX（缺少 ZIP/OOXML 头）", code="bad_magic")
        names = check_zip_limits("XLSX", kind, data)
        if "xl/workbook.xml" not in names:
            raise ParseError("文件内容不是有效 XLSX（缺少 xl/workbook.xml）", code="bad_magic")


def _clean(text: str) -> str:
    text = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x0c", "\n")
        .replace("\u3000", " ")
    )
    out: list[str] = []
    blank = False
    for line in text.split("\n"):
        line = line.rstrip()
        if not line.strip():
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    text = "\n".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ---------------- PDF ----------------

def parse_pdf(path: Path) -> list[Unit]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ParseError("PDF 已加密且无法解密", code="pdf_encrypted") from exc
    units: list[Unit] = []
    total = 0
    budget = ParseBudget("PDF")
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"第 {idx} 页文本提取失败：{exc}", code="pdf_extract") from exc
        text = _clean(text)
        budget.spend(text)
        total += len(text)
        if text:
            units.append(Unit(text=text, page=idx))
    if total == 0:
        raise ParseError(
            "PDF 中未提取到任何文本（可能是扫描件/图片型 PDF）。本试点不含 OCR，"
            "请改用带文字层的 PDF。",
            code="scanned_pdf",
        )
    check_units(units, "PDF")
    return units


# ---------------- DOC / DOCX ----------------

def parse_doc(path: Path) -> list[Unit]:
    try:
        result = subprocess.run(
            ["antiword", "-m", "UTF-8.txt", str(path)],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise ParseError("DOC 解析组件未安装，请联系管理员", code="doc_parser_missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParseError("DOC 解析超时，请检查文件是否损坏", code="doc_extract") from exc
    if result.returncode != 0:
        raise ParseError("DOC 读取失败，文件可能损坏、加密或格式不兼容", code="doc_extract")
    text = _clean(result.stdout.decode("utf-8", errors="replace"))
    ParseBudget("DOC").spend(text)
    units = [
        Unit(text=para, paragraph=index)
        for index, para in enumerate((part.strip() for part in re.split(r"\n\s*\n", text)), start=1)
        if para
    ]
    if not units:
        raise ParseError("DOC 中没有可索引的文本内容", code="empty_doc")
    check_units(units, "DOC")
    return units

def parse_docx(path: Path) -> list[Unit]:
    from docx import Document

    doc = Document(str(path))
    units: list[Unit] = []
    para_no = 0
    budget = ParseBudget("DOCX")

    def add(text: str) -> None:
        nonlocal para_no
        text = _clean(text)
        if text:
            budget.spend(text)
            para_no += 1
            units.append(Unit(text=text, paragraph=para_no))

    for p in doc.paragraphs:
        add(p.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                add(cell.text)
    if not units:
        raise ParseError("DOCX 中没有可索引的文本内容", code="empty_doc")
    check_units(units, "DOCX")
    return units


# ---------------- XLSX ----------------

def parse_xlsx(path: Path) -> list[Unit]:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"XLSX 读取失败：{exc}", code="xlsx_extract") from exc

    units: list[Unit] = []
    budget = ParseBudget("XLSX")
    try:
        for sheet in workbook.worksheets:
            for row_no, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = []
                for col_no, value in enumerate(row, start=1):
                    if value is None:
                        continue
                    text = _clean(str(value))
                    if text:
                        cells.append(f"{get_column_letter(col_no)}{row_no}={text}")
                if cells:
                    row_text = f"工作表《{sheet.title}》第 {row_no} 行：" + "；".join(cells)
                    budget.spend(row_text)
                    units.append(Unit(text=row_text, paragraph=row_no))
                    if len(units) > settings.parse_max_units:
                        raise ParseError(
                            f"XLSX 解析出的行数已超过 {settings.parse_max_units} 上限"
                            f"（当前工作表：{sheet.title}）。请拆分工作表，"
                            "或调大 RAG_PARSE_MAX_UNITS。",
                            code="too_many_units",
                        )
    finally:
        workbook.close()
    if not units:
        raise ParseError("XLSX 中没有可索引的单元格内容", code="empty_doc")
    return units


# ---------------- TXT / MD ----------------

def _read_text_bytes(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("文本编码无法识别（支持 UTF-8 / GB18030）", code="bad_encoding")


def parse_text(path: Path) -> list[Unit]:
    text = _clean(_read_text_bytes(path))
    units: list[Unit] = []
    para_no = 0
    budget = ParseBudget("文本")
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        budget.spend(para)
        para_no += 1
        units.append(Unit(text=para, paragraph=para_no))
    if not units:
        raise ParseError("文件内容为空，无可索引文本", code="empty_doc")
    check_units(units, "文本")
    return units


PARSERS: dict[str, object] = {
    "pdf": parse_pdf,
    "doc": parse_doc,
    "docx": parse_docx,
    "xlsx": parse_xlsx,
    "txt": parse_text,
    "md": parse_text,
}
