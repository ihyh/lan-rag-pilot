"""CEID条目边界、原文目录与有据回答；同号条目从不按置信度仲裁。"""
from __future__ import annotations

from bisect import bisect_right
from os.path import commonprefix
import re

from .parsing import Unit

_HEADER = re.compile(r"\bCEID\b\s*(?:Collection\s+Event\s+ID|(?:Event\s+)?(?:definitions?|description|name)|事件(?:定义|列表)|[|｜])", re.I)
_STOP = re.compile(r"Where\s+used\s*:|F\s*or\s+e\s*ach\s+alarm\s+will\s+send\s+an\s+event|(?m:^[ \t]*#{1,6}\s+(?!.*CEID).+$)", re.I)
_ROW = re.compile(r"(?im)^[ \t]*(?:CEID[ \t]*)?(\d{1,10})[ \t]*\s*=[ \t]*|^[ \t]*CEID[ \t]*[=:：][ \t]*(\d{1,10})[ \t]*[:：][ \t]*")
_SCENARIO = re.compile(r"(?:tool\s+)?platform\s*\d+|(?:E87\s*)?port\s*transfer|access\s+mode\s+(?:was\s+)?(?:enabled|disabled)", re.I)
_SECTION = re.compile(r"(?im)^.*(?:Below\s+are\s+the\s+CEID|业务场景\s*[:：]).*$")
_DETAIL = re.compile(r"(?:^|\n)\s*(?:Description|Definition|描述|定义|说明|详细描述)\s*[:：]\s*", re.I)


def event_spans(text: str, continuation: bool = False) -> list[dict]:
    """只识别明确CEID定义表/显式CEID行；普通数字、RCMD、SVID不当事件。"""
    headers = list(_HEADER.finditer(text))
    header_positions = [h.start() for h in headers]
    stop_positions = [m.start() for m in _STOP.finditer(text)]
    sections = list(re.finditer(r"Below\s+are\s+the\s+CEID\s+description\s+while\s+the\s+Access\s+mode\s+was\s+(enabled|disabled)", text, re.I))
    section_positions = [s.start() for s in sections]
    entries = []
    for match in _ROW.finditer(text):
        explicit = match.group(2) is not None or match.group().lstrip().upper().startswith("CEID")
        header_index = bisect_right(header_positions, match.start()) - 1
        section_start = header_positions[header_index] if header_index >= 0 else 0
        if not explicit and header_index < 0 and not continuation:
            continue
        stop_index = bisect_right(stop_positions, match.start()) - 1
        if not explicit and stop_index >= 0 and stop_positions[stop_index] >= section_start:
            continue
        entries.append(match)
    result = []
    for i, match in enumerate(entries):
        end = entries[i + 1].start() if i + 1 < len(entries) else len(text)
        stop = _STOP.search(text, match.end(), end)
        if stop:
            end = stop.start()
        # 下一节的业务条件属于下一条，不能粘到前一条定义尾部。
        heading = _SECTION.search(text, match.end(), end)
        if heading:
            end = heading.start()
        body = text[match.end():end].strip()
        if not body:
            continue
        detail = _DETAIL.search(body)
        name_text = body[:detail.start()] if detail else body
        name = " ".join(name_text.split())
        scenarios = [" ".join(m.group().split()) for m in _SCENARIO.finditer(name)]
        scenario_index = bisect_right(section_positions, match.start()) - 1
        if scenario_index >= 0:
            scenarios.append("Access mode " + sections[scenario_index].group(1).lower())
        scenario = " / ".join(dict.fromkeys(scenarios)) or "资料未标注业务场景"
        result.append(dict(ceid=(match.group(1) or match.group(2)).lstrip("0") or "0", event_name=name,
            scenario=scenario, full_definition=body, description_status="provided" if detail and body[detail.end():].strip() else "label_only",
            start=match.start(), end=end))
    return result


def event_units(units: list[Unit]) -> list[Unit]:
    """跨段落/页保留完整条目；非条目文本仍按原页交给通用切块器。"""
    if any(u.ceid_event is not None for u in units):
        result, pending = [], []
        for unit in units:
            if unit.ceid_event is None:
                pending.append(unit)
            else:
                result.extend(event_units(pending))
                pending = []
                result.append(unit)
        return result + event_units(pending)
    starts, cursor = [], 0
    for unit in units:
        starts.append(cursor)
        cursor += len(unit.text) + 1
    raw_text = "\n".join(u.text for u in units)
    text = raw_text
    pages = [u.text for u in units if u.page is not None]
    # 多页确实重复的页眉按位置屏蔽，防止页眉变成上一条的事件名称；不改原chunk文字。
    prefix = commonprefix(pages) if len(pages) > 1 else ""
    prefix = prefix[:prefix.rfind("\n") + 1]
    if len(prefix) >= 30 and prefix.count("\n") >= 2:
        masked = list(text)
        for start, unit in zip(starts, units):
            if unit.page is not None and unit.text.startswith(prefix):
                number = re.match(r"[0-9]+[ \t]*\n", unit.text[len(prefix):])
                end = start + len(prefix) + (number.end() if number else 0)
                masked[start:end] = ["\n" if c == "\n" else " " for c in text[start:end]]
        text = "".join(masked)
    events = event_spans(text)
    if not events:
        return units
    output = []

    def ordinary(start, end):
        while start < end:
            index = bisect_right(starts, start) - 1
            unit = units[index]
            boundary = min(end, starts[index] + len(unit.text))
            part = raw_text[start:boundary].strip()
            if part:
                output.append(Unit(part, unit.page, unit.paragraph))
            start = min(end, starts[index] + len(unit.text) + 1)

    pos = 0
    for event in events:
        ordinary(pos, event["start"])
        unit = units[bisect_right(starts, event["start"]) - 1]
        last = units[bisect_right(starts, max(event["start"], event["end"] - 1)) - 1]
        metadata = {k: v for k, v in event.items() if k not in {"start", "end"}}
        metadata["page_end"] = last.page
        output.append(Unit(raw_text[event["start"]:event["end"]].strip(), unit.page, unit.paragraph, metadata))
        pos = event["end"]
    ordinary(pos, len(text))
    return output


def table_event(headers: list[str], cells: list[str]) -> dict | None:
    """DOCX/XLSX显式列头目录；缺少列头时不猜数字列的角色。"""
    roles = {"ceid": "ceid", "eventid": "ceid", "事件id": "ceid", "事件编号": "ceid",
             "eventname": "name", "事件名称": "name", "name": "name",
             "scenario": "scenario", "业务场景": "scenario", "场景": "scenario",
             "description": "description", "definition": "description", "完整定义": "description", "描述": "description"}
    values = {roles[key]: str(value).strip() for header, value in zip(headers, cells)
              if (key := re.sub(r"[\s_-]", "", header).lower()) in roles}
    if not re.fullmatch(r"[0-9]{1,10}", values.get("ceid", "")):
        return None
    name = values.get("name") or values.get("description", "").split("\n")[0]
    if not name:
        return None
    scenario = values.get("scenario") or " / ".join(" ".join(m.group().split()) for m in _SCENARIO.finditer(name)) or "资料未标注业务场景"
    return dict(ceid=values["ceid"].lstrip("0") or "0",event_name=name,scenario=scenario,
        full_definition=" | ".join(cells),description_status="provided" if values.get("description") and values.get("name") else "label_only",page_end=None)


def query_ids(question: str) -> set[str]:
    matches = re.findall(r"(?:\bCEID\b|事件\s*(?:ID|编号)|event\s*id)\s*(?:为|是|[=：:])?\s*([0-9]+(?:\s*[,，、/]\s*[0-9]+)*)", question, re.I)
    return {n.lstrip("0") or "0" for m in matches for n in re.findall(r"[0-9]+", m)}


def mentioned_ids(text: str) -> set[str]:
    return {n.lstrip("0") or "0" for n in re.findall(r"\bCEID\s*[=:：]?\s*([0-9]{1,10})(?![0-9])", text, re.I)}


def name_terms(question: str) -> list[str]:
    match = re.search(r"([A-Za-z][A-Za-z _-]+?)\s*(?:的)?\s*(?:事件\s*(?:ID|编号)|CEID\b)", question, re.I)
    if not match:
        return []
    terms = re.findall(r"[a-z]+", match.group(1).lower())
    # cycle查询按开始事件查号；名称保留原文，完成/卸载事件不混入开始事件。
    if "cycle" in terms and not any(t in terms for t in ("started", "completed", "abort")):
        terms.append("started")
    return [t for t in terms if t != "cycle"]


def is_query(question: str) -> bool:
    return bool(query_ids(question) or name_terms(question))


def matches_name(terms: list[str], name: str) -> bool:
    words = re.findall(r"[a-z]+", name.lower())
    return bool(terms and set(terms) <= set(words) and
                ("load" not in terms or words[0] == "load"))


def relevant(question: str, events: list[dict]) -> list[dict]:
    ids = query_ids(question)
    if not ids:
        terms = name_terms(question)
        if not terms:
            return []
        ids = {e["ceid"] for e in events if matches_name(terms, e["event_name"])}
    return [e for e in events if e["ceid"] in ids]


def catalog(db, rows) -> dict[int, list[dict]]:
    """新库读结构化目录，旧库从实际检索原文兼容提取，保留重复号。"""
    result = {}
    indexed_docs = set()
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ceid_events'").fetchone():
        for row in db.execute("SELECT e.* FROM ceid_events e JOIN documents d ON d.id=e.document_id WHERE d.status='ready' ORDER BY e.id"):
            event = dict(row)
            result.setdefault(int(event["chunk_id"]), []).append(event)
            indexed_docs.add(event["document_id"])
    continuation = {}
    for row in rows:
        did = int(row["document_id"])
        if did in indexed_docs:
            continue
        text = row["content"]
        events = event_spans(text, continuation.get(did, False))
        headers = list(_HEADER.finditer(text))
        stops = list(_STOP.finditer(text))
        if headers:
            continuation[did] = True
        if stops and (not headers or stops[-1].start() > headers[-1].start()):
            continuation[did] = False
        for event in events:
            result.setdefault(int(row["chunk_id"]), []).append({k: v for k, v in event.items() if k not in {"start", "end"}})
    return result


def select_sources(sources: list[dict], k: int, question: str) -> list[dict] | None:
    all_events = [dict(e, chunk_id=s["chunk_id"]) for s in sources for e in s.get("ceid_events", [])]
    entries = relevant(question, all_events)
    event_ids = query_ids(question) or {e["ceid"] for e in entries}
    ids = {e["chunk_id"] for e in entries} | {s["chunk_id"] for s in sources if event_ids & set(s.get("ceid_ids", []))}
    if not ids:
        return None
    pinned = [s for s in sources if s["chunk_id"] in ids]
    if sum(len(s["content"]) for s in pinned) > 16000:
        raise ValueError("同一CEID的全部资料超出单次上下文预算，请限定文档范围后重试；未截取部分定义作答。")
    return pinned + [s for s in sources if s["chunk_id"] not in ids][:max(0, k - len(pinned))]


def answer(question: str, sources: list[dict]) -> str | None:
    if not is_query(question):
        return None
    entries = []
    for number, source in enumerate(sources, 1):
        events = source.get("ceid_events")
        if events is None:
            events = event_spans(source["content"])
        entries.extend(dict(e, source_number=number) for e in events)
    selected = relevant(question, entries)
    if not selected:
        related = [(number, source) for number, source in enumerate(sources, 1)
                   if query_ids(question) & set(source.get("ceid_ids", []))]
        if related:
            return "\n".join(f"仅检索到同ID关联片段，现有资料没有该条目的完整描述；业务场景需人工核对。[{number}]\n```text\n{source['content']}\n```" for number, source in related)
        return "现有检索资料没有可确认的对应CEID条目，无法据此给出事件定义或编号。"
    terms = name_terms(question)
    direct = query_ids(question)
    lines = []
    if not direct:
        primary = [e for e in selected if matches_name(terms, e["event_name"])]
        lines.append("对应的事件ID：" + "；".join(f"{e['ceid']}（{e['scenario']}，{e['event_name']}）[{e['source_number']}]" for e in primary) + "。")
    for event in selected:
        ref = f"[{event['source_number']}]"
        lines.append(f"CEID={event['ceid']}｜业务场景：{event['scenario']}｜检索条目：{event['event_name']}。{ref}")
        if event["description_status"] == "provided":
            lines.append(f"条目原文：{event['full_definition']} {ref}")
        else:
            lines.append(f"现有资料没有该条目的完整描述。{ref}")
    for ceid in dict.fromkeys(e["ceid"] for e in selected):
        variants = [e for e in selected if e["ceid"] == ceid]
        if len({(e["scenario"], " ".join(e["full_definition"].split())) for e in variants}) > 1:
            refs = "".join(dict.fromkeys(f"[{e['source_number']}]" for e in variants))
            lines.append(f"注意：CEID={ceid}在现有资料中有不同定义或业务场景，存在同ID不同含义；以上逐条保留，请结合场景人工甄别。{refs}")
    wanted = direct or {e["ceid"] for e in selected}
    for number, source in enumerate(sources, 1):
        if wanted & set(source.get("ceid_ids", [])) and not any(e["source_number"] == number for e in selected):
            lines.append(f"其他同ID关联片段（未识别为完整事件定义，业务场景需人工核对）：\n```text\n{source['content']}\n```\n[{number}]")
    return "\n".join(lines)
