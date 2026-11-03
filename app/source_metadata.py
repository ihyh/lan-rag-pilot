"""文档来源类型判定与解析；普通文档与旧记录返回 None。"""
from __future__ import annotations

import json

from .web_content import metadata as web_metadata
from .log_content import metadata as log_metadata

SOURCE_TYPES = ("file", "web", "image", "log")

_SOURCE_IMAGE_SQL = ("(lower(d.content_type) LIKE 'image/%' OR lower(d.filename) LIKE '%.png' "
                     "OR lower(d.filename) LIKE '%.jpg' OR lower(d.filename) LIKE '%.jpeg')")
_SOURCE_WEB_SQL = ("(COALESCE(d.source_metadata LIKE '%\"kind\": \"web\"%' OR d.source_metadata LIKE '%\"kind\":\"web\"%', 0))")
_SOURCE_LOG_SQL = ("(COALESCE(d.source_metadata LIKE '%\"kind\": \"log\"%' OR d.source_metadata LIKE '%\"kind\":\"log\"%', 0))")


def source_type_sql(kind: str) -> str:
    """返回 documents 表（别名 d）上的来源类型 SQL 条件。"""
    if kind == "image":
        return _SOURCE_IMAGE_SQL
    if kind == "web":
        return _SOURCE_WEB_SQL
    if kind == "log":
        return _SOURCE_LOG_SQL
    if kind == "file":
        return "(NOT " + _SOURCE_IMAGE_SQL + " AND NOT " + _SOURCE_WEB_SQL + " AND NOT " + _SOURCE_LOG_SQL + ")"
    raise ValueError(f"未知资料类型：{kind}")


def document_source_type(raw_metadata, content_type, filename) -> str:
    """按原始 source_metadata 文本判断来源类型；解析失败也不影响分类。"""
    if isinstance(raw_metadata, dict):
        raw = json.dumps(raw_metadata, ensure_ascii=False)
    elif isinstance(raw_metadata, str):
        raw = raw_metadata
    else:
        raw = ""
    if '"kind": "web"' in raw or '"kind":"web"' in raw:
        return "web"
    if '"kind": "log"' in raw or '"kind":"log"' in raw:
        return "log"
    text_type = str(content_type or "").lower()
    text_name = str(filename or "").lower()
    if text_type.startswith("image/") or text_name.endswith((".png", ".jpg", ".jpeg")):
        return "image"
    return "file"


def parse_metadata(value):
    return web_metadata(value) or log_metadata(value)
