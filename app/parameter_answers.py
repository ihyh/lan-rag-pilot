"""命令参数列表（Byte N）的确定性回答（实验，默认关闭）。

真实来源里配置命令的参数定义常写成多行：
    Byte 1: Inter-Character time-out in tenths of seconds (must be >4: if not, reverts to default value of 40).
模型容易把“Byte3 消息重试次数”也概括成时间单位，或把默认值/限制抄错。这里只在原文
逐字可核对时，按 `Byte N: ...` 块拼装回答：

- 识别问题里点名的 Byte 编号（Byte1/Byte2…）；
- 逐条保留明确的 Byte 定义，合并括号内折行；归属不明的续行回退；
- 只输出原文明确写出的控制内容、`must be >N` 限制、`default … N` 默认值、
  `in tenths of seconds` 之类的单位；
- 原文没有单位就写“未给出计量单位”，不补“count/次”等；
- 找不到对应 Byte 块时返回 None，交回原生成路径。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext

BYTE_BLOCK = re.compile(r"(?<![A-Za-z0-9_])Byte\s*(\d+)\s*[:：]\s*", re.IGNORECASE)
BYTE_ASK = re.compile(r"(?<![A-Za-z0-9_])Byte\s*(\d+)(?![A-Za-z0-9_.])", re.IGNORECASE)
LIMIT = re.compile(r"\bmust\s+be\s*>\s*(\S+)", re.IGNORECASE)
DEFAULT = re.compile(r"\bdefault(?:\s+value)?\s*(?:of\s*)?(\S+)", re.IGNORECASE)
NUMBER = re.compile(r"[+-]?(?:0[xX][\da-fA-F]+|(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)")
NEGATION = re.compile(r"\b(?:not|no|never|without|neither|cannot)\b|n['’]t\b|不|非|未|无", re.IGNORECASE)
SECTION = re.compile(r"^(?:response|reply|result|notes?)\b|^[\w /-]+[:：]", re.IGNORECASE)
TIME_UNITS = r"tenths? of a second|tenths? of seconds|milliseconds?|seconds?|ms"
UNIT = re.compile(rf"\bin\s+({TIME_UNITS})\b", re.IGNORECASE)
CONVERT_ASK = re.compile(
    r"\s*(?:请\s*)?(?:(?:将|把)\s*)?Byte\s*\d+(?:\s*[、,，和及]\s*Byte\s*\d+)*\s*"
    r"(?:的\s*)?默认值\s*(?:分别\s*)?"
    r"(?:(?:换算|转换|折合)(?:成|为|到)?|(?:是|等于)?(?:多少|几))\s*"
    r"(?P<target>milliseconds?|seconds?|ms|s|毫秒|秒)\s*(?:是多少|为多少)?\s*[?？。.]?\s*",
    re.IGNORECASE)
CALC_INTENT = re.compile(r"换算|转换|折合|计算|相加|相减|乘|除|总共|合计|差值|多久|秒|分钟|[+*/×÷]|\b(?:ms|seconds?|milliseconds?)\b",
                         re.IGNORECASE)
CONVERT_DEFINITION = re.compile(
    rf"(?P<name>[^()\n;；:：.]+?)\s+in\s+(?P<unit>{TIME_UNITS})\s*\(\s*"
    rf"(?:must\s+be\s*>\s*{NUMBER.pattern}\s*(?:;|:\s*if\s+(?:not|no),\s*(?:it\s+)?reverts\s+to)\s*)?"
    rf"default(?:\s+value)?\s*(?:of\s*)?(?P<value>{NUMBER.pattern})\s*\)\.?",
    re.IGNORECASE)
ATTRIBUTE_WORDS = {
    "control": ("控制", "功能", "控制什么"),
    "limit": ("限制", "必须", "大于"),
    "default": ("默认",),
    "unit": ("单位", "计量"),
}


def _blocks(content: str, *, strict: bool = False) -> list[tuple[int, str]] | None:
    """保留重复定义；仅合并可判定为括号续段的PDF折行。"""
    blocks: list[tuple[int, str]] = []
    current: int | None = None
    for raw in (content or "").replace("\x00", "").splitlines():
        line = raw.strip()
        matches = list(BYTE_BLOCK.finditer(line))
        if matches:
            prefix = line[:matches[0].start()].strip(" ;；")
            if prefix and not re.fullmatch(r"Parameters?\s*[:：]", prefix, re.IGNORECASE):
                if current is None or SECTION.search(prefix):
                    if strict:
                        return None
                    current = None
                    continue
                number, text = blocks[current]
                if text.count("(") <= text.count(")"):
                    return None
                blocks[current] = (number, (text + " " + prefix).strip())
            for pos, match in enumerate(matches):
                end = matches[pos + 1].start() if pos + 1 < len(matches) else len(line)
                blocks.append((int(match[1]), line[match.end():end].strip(" ;；")))
                current = len(blocks) - 1
        elif not line or SECTION.search(line):
            if strict and line and not re.fullmatch(r"Parameters?\s*[:：]", line, re.IGNORECASE):
                return None
            current = None
        elif current is not None:
            number, text = blocks[current]
            if text.count("(") > text.count(")") or line.startswith("("):
                blocks[current] = (number, (text + " " + line).strip())
            else:
                return None
        elif strict:
            return None
    return blocks


def _numeric_value(pattern: re.Pattern, text: str) -> str | None:
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    values = []
    for match in matches:
        # 只检查当前分句，不能因其他属性被否定就丢弃本属性。
        # 括号/逗号不切断否定作用域；仅豁免明确的条件回退写法。
        clause = re.split(r"[;；。!?！？]|\.(?=\s|$)", text[:match.start()])[-1]
        if pattern is DEFAULT:
            clause = re.sub(r"\bif\s+(?:not|no)\s*,\s*(?:it\s+)?reverts\s+to\s*$", "", clause,
                            flags=re.IGNORECASE)
        if NEGATION.search(clause):
            return None
        token = match[1]
        # 数字后还有未解析续项时不认作单值，如“1 - 3”“40 or 80”。
        if text[match.end():].strip() and not token.endswith((")", ";", "；", "。", ".", ":", "：")):
            return None
        value = token.rstrip(");；，:：。").removesuffix(".").rstrip(");；，:：。")
        values.append(value)
    if all(NUMBER.fullmatch(value) for value in values) and len(set(values)) == 1:
        return values[0]
    return None


def _numeric_detail(pattern: re.Pattern, text: str, label: str, prefix: str) -> str:
    if not pattern.search(text):
        return f"已识别的Byte定义中未明确提供{label}"
    value = _numeric_value(pattern, text)
    if value is not None:
        return prefix + value
    return f"{label}未能唯一、完整提取，请核对定义文本"


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _conversion_detail(text: str, target: str) -> str:
    if len(list(UNIT.finditer(text))) != 1:
        return "未计算：缺少唯一、受支持的时间单位，请核对定义文本"
    literal = _numeric_value(DEFAULT, text)
    if literal is None:
        return "未计算：默认值或定义未能可靠确认，请核对原文"
    definition = CONVERT_DEFINITION.fullmatch(text)
    if definition is None or NEGATION.search(definition["name"]):
        return "未计算：定义中的数值、单位归属或适用条件未能可靠确认"
    if definition["unit"].lower() == "ms" and definition["unit"] != "ms":
        return "未计算：单位缩写大小写不在支持范围内，请核对原文"
    # ponytail: 仅做闭合Byte默认时间定义的单位换算；其它公式/语义交回原流程。
    if len(literal) > 32:
        return "未计算：数值超出当前换算范围"
    try:
        value = Decimal(int(literal, 16)) if "0x" in literal.lower() else Decimal(literal)
    except (InvalidOperation, ValueError):
        return "未计算：数值超出当前换算范围"
    if value < 0 or abs(value.adjusted()) > 12 or abs(value.as_tuple().exponent) > 24:
        return "未计算：数值超出当前换算范围；负值可能是特殊编码，不能直接作为时长"
    unit = definition["unit"].lower()
    target_name = "毫秒" if target.lower() in ("毫秒", "ms", "millisecond", "milliseconds") else "秒"
    with localcontext() as context:
        context.prec = max(28, len(literal) + 6)
        factor = Decimal("0.1") if unit.startswith("tenth") else (
            Decimal("0.001") if unit == "ms" or unit.startswith("millisecond") else Decimal(1))
        if target_name == "毫秒":
            factor *= 1000
        result = value * factor
    return (f"计算结果：{_decimal_text(value)} × {_decimal_text(factor)} = {_decimal_text(result)} {target_name}；"
            f"原文默认值 {literal}，单位 {definition['unit']}；"
            f"换算依据：1 {definition['unit']} = {_decimal_text(factor)} {target_name}")


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
    # 本分支不解析自然语言文件范围，留给原有检索范围和生成路径。
    if re.search(r"\.[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9_])", question):
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
    conversion = CONVERT_ASK.fullmatch(question)
    if conversion is not None and conversion["target"].lower() in ("ms", "s") and conversion["target"] not in ("ms", "s"):
        return None
    if conversion is None and CALC_INTENT.search(question):
        return None
    blocks: list[tuple[int, str, int]] = []
    unbound_context: set[int] = set()
    for source_index, source in enumerate(sources, start=1):
        definitions = _blocks(source.get("content") or "")
        if definitions is None:
            return None
        if conversion is not None and _blocks(source.get("content") or "", strict=True) is None:
            unbound_context.add(source_index)
        for number, text in definitions:
            blocks.append((number, text, source_index))
    if not blocks:
        return None
    if not any(number in requested for number, _, _ in blocks):
        return None
    lines: list[str] = []
    for number in requested:
        records = [(text, index) for byte, text, index in blocks if byte == number]
        if not records:
            lines.append(f"Byte {number}：检索片段未提供该参数的定义。")
            continue
        if len(records) > 1:
            lines.append(f"Byte {number} 有多条资料记录，以下分别列出；请核对设备、命令和版本，未自动取舍。")
        for text, source_index in records:
            cite = f"[{source_index}]"
            control = _control_phrase(text)
            details = []
            if "control" in attributes:
                details.append(f"控制 {control}" if control else "控制内容未明确写出")
            if "limit" in attributes:
                details.append(_numeric_detail(LIMIT, text, "限制", "限制必须大于 "))
            if "default" in attributes:
                details.append(_numeric_detail(DEFAULT, text, "默认值", "默认 "))
            if "unit" in attributes:
                units = list(UNIT.finditer(text))
                unit = units[0] if len(units) == 1 else None
                negated = unit and re.search(r"\b(?:not|no|never|without)\b|不|非", text[:unit.start()], re.IGNORECASE)
                details.append(f"单位 {unit[1]}" if unit and not negated else
                               "原文未给出计量单位" if not units else "单位未能可靠提取，请核对定义文本")
            if conversion is not None:
                details = ["未计算：来源含未能归属的说明或条件，请先核对原文" if source_index in unbound_context
                           else _conversion_detail(text, conversion["target"])]
            filename = sources[source_index - 1].get("filename") or "未命名来源"
            lines.append(f"Byte {number}：{'；'.join(details)}（文件《{filename}》）{cite}")
            if source_index in unbound_context:
                lines.append(f"来源原文（含未归属上下文）：{sources[source_index - 1]['content']} {cite}")
            else:
                lines.append(f"定义文本（折行合并）：{text or '未提供描述'} {cite}")
    return {
        "answer": "\n".join(lines),
        "model": "parameter_answers",
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
