"""Check title-based ordering within the already permitted candidate set."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["RAG_EMBED_BACKEND"] = "mock"
os.environ["RAG_SECRET_KEY"] = "test-secret"

from app.routers.query import _select_sources


def source(cid, filename):
    return {"chunk_id": cid, "document_id": cid, "filename": filename,
            "content": f"Candidate evidence {cid}.", "score": 1 - cid / 100,
            "page": 1, "seq": 0}


def main():
    diagram = source(1, "Unit_A diagram.png")
    irrelevant = source(2, "Other manual.pdf")
    manual = source(3, "Unit A PanelWeb manual.pdf")
    candidates = [diagram, irrelevant, manual]
    selected = _select_sources(candidates, 2, "Unit_A PanelWeb 对应哪张图和哪份手册？")
    assert selected == [diagram, manual], selected
    assert all(item in candidates for item in selected)
    assert _select_sources([irrelevant, manual], 1, "PanelWebExtra 对应哪份手册？") == [irrelevant]
    assert _select_sources([irrelevant, manual], 1, "哪份手册？") == [irrelevant]
    assert _select_sources([irrelevant, manual], 1, "PanelWeb 对应哪份手册？", preserve_ranking=True) == [irrelevant]
    assert _select_sources([irrelevant], 1, "PanelWeb 对应哪份手册？") == [irrelevant]
    next_page = dict(manual, chunk_id=4, content="More evidence.", page=2, seq=1)
    assert _select_sources([irrelevant, manual, next_page], 2, "PanelWeb 怎么用？") == [manual, irrelevant], \
        "title priority must not promote every chunk of one document"

    # A protocol may be named in a real worksheet title rather than the filename.
    protocol_manual = source(10, "MODEL-10 ASCII manual.pdf")
    row = dict(source(11, "MODEL-10 测试.xlsx"), content=(
        "工作表《ASCII协议测试》第 3 行：C3=关闭OPEN互锁指令功能；"
        "E3=发送指令：HCS DISABLE OPEN<CR><LF>"),
        table_headers={"ASCII协议测试": {"columns": {"C": "测试项", "E": "测试步骤"}}})
    q = "MODEL-10 关闭 OPEN 互锁指令功能要发送什么 ASCII 指令？"
    pool = [protocol_manual, row]
    assert _select_sources(pool, 1, q) == [row], "keep the full matching command row"
    from app.llm import _source_projection
    assert _source_projection(q, _select_sources(pool, 1, q)) == \
        "测试记录中的发送指令：HCS DISABLE OPEN<CR><LF>。[1]"
    assert _select_sources(pool, 1, q, preserve_ranking=True) == [protocol_manual]
    for content, filename in [
        (row["content"].replace("ASCII协议测试", "HEX协议测试") + "\n说明中提到 ASCII", row["filename"]),
        (row["content"], "MODEL-10.txt"),
        (row["content"] + "\n工作表《HEX》第 4 行：C4=其它操作", row["filename"]),
        (row["content"].replace("ASCII协议测试", "ASCII_EXTRA"), row["filename"]),
        (row["content"], "MODEL-10 HEX 测试.xlsx"),
    ]:
        ambiguous = dict(row, content=content, filename=filename)
        assert _select_sources([protocol_manual, ambiguous], 1, q) == [protocol_manual], (filename, content)
    assert _select_sources([protocol_manual], 1, q) == [protocol_manual], "never add unseen candidates"
    print("retrieval title match PASS")


if __name__ == "__main__":
    main()
