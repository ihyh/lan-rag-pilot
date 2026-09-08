"""旧版 Word .doc 解析边界的轻量回归检查。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import parsing


OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def main() -> None:
    assert parsing.detect_ext("设备手册.DOC") == ".doc"
    parsing.check_mime("application/msword")
    parsing.check_magic("doc", OLE_HEADER + b"test")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "设备手册.doc"
        path.write_bytes(OLE_HEADER + b"test")
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="第一段\n\n第二段".encode("utf-8"), stderr=b""
        )
        with patch("app.parsing.subprocess.run", return_value=completed) as run:
            units = parsing.parse_doc(path)
        assert [unit.text for unit in units] == ["第一段", "第二段"]
        assert [unit.paragraph for unit in units] == [1, 2]
        assert run.call_args.args[0][:3] == ["antiword", "-m", "UTF-8.txt"]

    try:
        parsing.check_magic("doc", b"not-a-doc")
    except parsing.ParseError as exc:
        assert exc.code == "bad_magic"
    else:
        raise AssertionError("伪造 DOC 未被拒绝")

    project = Path(__file__).resolve().parents[1]
    admin_html = (project / "app/templates/admin.html").read_text(encoding="utf-8")
    admin_js = (project / "app/static/js/admin.js").read_text(encoding="utf-8")
    assert 'accept=".pdf,.doc,.docx,.xlsx,.txt,.md"' in admin_html
    assert "仅在处理失败或检索异常时使用" in admin_html
    assert "重建索引" in admin_js and "不会重复上传文件" in admin_js

    print("DOC_PARSING_CHECK=PASS")


if __name__ == "__main__":
    main()
