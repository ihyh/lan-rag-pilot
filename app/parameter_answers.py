"""命令参数列表（Byte N）的确定性回答（实验，默认关闭）。

真实来源里配置命令的参数定义常写成多行：
    Byte 1: Inter-Character time-out in tenths of seconds (must be >4: if not, reverts to default value of 40).
模型容易把“Byte3 消息重试次数”也概括成时间单位，或把默认值/限制抄错。这里只在原文
逐字可核对时，按 `Byte N: ...` 块拼装回答：

- 识别问题里点名的 Byte 编号（Byte1/Byte2…）；
- 从检索片段解析对应的 Byte 块（合并 PDF 折行）；
- 只输出原文明确写出的控制内容、`must be >N` 限制、`default … N` 默认值、
  `in tenths of seconds` 之类的单位；
- 原文没有单位就写“未给出计量单位”，不补“count/次”等；
- 找不到对应 Byte 块时返回 None，交回原生成路径。
"""
from __future__ import annotations

import re

BYTE_BLOCK = re.compile(r"\bByte\s*(\d+)\s*[:：]\s*(.*)$", re.IGNORECASE)
BYTE_ASK = re.compile(r"Byte\s*(\d+)", re.IGNORECASE)
LIMIT = re.compile(r"must\s+be\s*>\s*(\d+)", re.IGNORECASE)
DEFAULT = re.compile(r"default(?:\s+value)?\s*(?:of\s*)?(\d+)", re.IGNORECASE)
UNIT = re.compile(r"\bin\s+(tenths? of a second|tenths? of seconds|milliseconds?|seconds?|ms)\b",
                  re.IGNORECASE)
ATTRIBUTE_WORDS = {
    "control": ("控制", "功能", "控制什么"),
    "limit": ("限制", "必须", "大于"),
    "default": ("默认",),
    "unit": ("单位", "计量"),
}


def _blocks(content: str) -> dict[int, str]:
    """返回 {byte_number: 合并后的块文本}；PDF 折行会并入同一块。"""
    blocks: dict[int, str] = {}
    current: int | None = None
    for raw in (content or "").replace("\x00", "").splitlines():
        line = raw.strip()
        match = BYTE_BLOCK.search(line)
        if match:
            current = int(match.group(1))
            blocks[current] = match.group(2).strip()
        elif current is not None and line:
            blocks[current] = (blocks[current] + " " + line).strip()
    return blocks


def _requested_attributes(question: str) -> list[str]:
    found = []
    for attribute, words in ATTRIBUTE_WORDS.items():
        if any(word.lower() in question.lower() for word in words):
            found.append(attribute)
    return found


def _control_phrase(text: str) -> str:
    part = re.split(r"\s+in\s+(?:tenths? of (?:a second|seconds)|milliseconds?|seconds?|ms)\b",
                    text, maxsplit=1, flags=re.IGNORECASE)[0]
    part = re.split(r"\s*\(must\s+be", part, maxsplit=1, flags=re.IGNORECASE)[0]
    return part.strip(" .;；")


def build_parameter_answer(question: str, sources: list[dict]) -> dict | None:
    if not question or not sources:
        return None
    requested = []
    for match in BYTE_ASK.finditer(question):
        number = int(match.group(1))
        if number not in requested:
            requested.append(number)
    if not requested:
        return None
    attributes = _requested_attributes(question)
    if not attributes:
        return None
    blocks: dict[int, tuple[str, int]] = {}
    for source_index, source in enumerate(sources, start=1):
        for number, text in _blocks(source.get("content") or "").items():
            blocks.setdefault(number, (text, source_index))
    if not blocks:
        return None
    if not any(number in blocks for number in requested):
        return None
    lines: list[str] = []
    for number in requested:
        if number not in blocks:
            lines.append(f"Byte {number}：检索片段未提供该参数的定义。")
            continue
        text, source_index = blocks[number]
        cite = f"[{source_index}]"
        control = _control_phrase(text)
        details = []
        if "control" in attributes:
            details.append(f"控制 {control}" if control else "控制内容未明确写出")
        limit = LIMIT.search(text)
        if "limit" in attributes and limit:
            details.append(f"限制必须大于 {limit.group(1)}")
        default = DEFAULT.search(text)
        if "default" in attributes and default:
            details.append(f"默认 {default.group(1)}")
        if "unit" in attributes:
            unit = UNIT.search(text)
            if unit:
                details.append(f"单位 {unit.group(1)}")
            elif "count" in text.lower() or "retry" in text.lower():
                details.append("原文未给出计量单位（该项是重试次数，不是时间单位）")
            else:
                details.append("原文未给出计量单位")
        lines.append(f"Byte {number}：{'；'.join(details)} {cite}")
    return {
        "answer": "\n".join(lines),
        "model": "parameter_answers",
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
