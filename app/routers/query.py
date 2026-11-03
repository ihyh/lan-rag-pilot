"""问答（检索 + 模型调用）与个人问答历史。"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .. import acl, audit, figure_links, log_content, runtime as rt, vision, web_content
from ..source_metadata import parse_metadata
from ..config import settings
from ..context_assembly import attach_continuations
from ..db import atomic, connect, get_db, now_iso
from ..deps import require_kb_admin, require_user
from ..embeddings import EmbeddingUnavailable, embedding_service
from ..evidence_completion import attach_literal_evidence
from ..gate import llm_gate
from ..index import technical_terms, semantic_queries, vector_index
from ..image_caption_evidence import ImageCaptionEvidenceError, attach_image_captions
from ..llm import LLMError, _normalize_answer, chat as llm_chat, stream_chat as llm_stream_chat
from ..ratelimit import SlidingWindowLimiter
from ..reranking import RerankUnavailable, reranker
from ..schemas import MAX_SQLITE_ID, FeedbackBody, QueryBody

router = APIRouter()

query_limiter = SlidingWindowLimiter(limit=settings.queries_per_minute, window_seconds=60.0)

EMPTY_KB_ANSWER = "知识库当前为空：还没有任何可检索的文档。请联系管理员上传文档后再提问。"
HISTORY_TURNS = 3
HISTORY_CHAR_LIMIT = 2000
FOLLOWUP_MAX_ANCESTORS = 4
FOLLOWUP_CHAR_LIMIT = 2000


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _snapshot_sources(db: sqlite3.Connection, sources: list[dict]) -> list[dict]:
    """冻结来源身份；正文保留原样，不把检索片段冒充完整文档或最终 prompt。"""
    frozen = []
    for source in sources:
        item = dict(source)
        if "source_snapshot" not in item:
            row = db.execute(
                "SELECT filename,stored_name,sha256,version,effective_date,applicability,device,source_metadata,tags FROM documents WHERE id=?",
                (item["document_id"],),
            ).fetchone()
            item["source_snapshot"] = ({"format": 1, "filename": row["filename"],
                "document_sha256": row["sha256"], "version": row["version"],
                "effective_date": row["effective_date"], "applicability": row["applicability"], "device": row["device"]}
                if row else None)
            if row and Path(row['stored_name']).suffix.lower() in {'.png','.jpg','.jpeg'}:
                item['source_snapshot'].update(source_kind='image',content_scope='image_visual_and_text' if item.get('_visual_image') else 'image_text_only')
                try:
                    tags = json.loads(row['tags'] or '[]')
                except (TypeError, ValueError):
                    tags = []
                if isinstance(tags, list):
                    if 'image_caption_only' in tags:
                        item['source_snapshot']['image_caption_only'] = True
                    if 'ocr_human_review_pending' in tags:
                        item['source_snapshot']['ocr_human_review_pending'] = True
                    if 'web_image_shared_source' in tags:
                        item['source_snapshot']['web_image_shared_source'] = True
            if row and 'source_metadata' in row.keys():
                metadata = parse_metadata(row['source_metadata'])
                log_identity = log_content.is_log_document(row)
                if metadata and metadata['sha256'] == row['sha256'] and (not log_identity or metadata['kind']=='log'):
                    item['source_snapshot'].update(source_kind=metadata['kind'], **{metadata['kind']:metadata})
                    if metadata['kind']=='log':
                        chunk = db.execute('SELECT line_start,line_end FROM chunks WHERE id=? AND document_id=?',
                            (item.get('chunk_id'),item['document_id'])).fetchone()
                        start,end = (chunk['line_start'],chunk['line_end']) if chunk else (None,None)
                        item['source_snapshot'].update(line_start=start,line_end=end)
                        item.update(line_start=start,line_end=end)
                elif row['source_metadata'] is not None or log_identity:
                    # 来源损坏不能退成可核对的普通文档身份。
                    item['source_snapshot'] = {'format':0}
        if item.get('source_snapshot') and item.get('related_images'):
            item['source_snapshot']['related_images'] = item['related_images']
        frozen.append(item)
    return frozen


def _source_payload(source: dict) -> dict:
    """同步、流式和历史共用契约；不从当前索引重造旧证据。"""
    snapshot = source.get("source_snapshot")
    legacy = snapshot is None
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except ValueError:
            snapshot = None
    valid = (isinstance(snapshot, dict) and type(snapshot.get("format")) is int
        and snapshot["format"] == 1
        and all(isinstance(snapshot.get(key), str) for key in
                ("filename", "document_sha256", "version", "applicability"))
        and bool(snapshot["filename"].strip())
        and re.fullmatch(r"[0-9a-fA-F]{64}", snapshot["document_sha256"]) is not None
        and "effective_date" in snapshot
        and (snapshot.get("effective_date") is None or isinstance(snapshot["effective_date"], str)))
    if valid and snapshot.get('source_kind') == 'web':
        web = web_content.metadata(snapshot.get('web'))
        valid = web is not None and web['sha256'] == snapshot['document_sha256']
    if valid and snapshot.get('source_kind') == 'log':
        log = log_content.metadata(snapshot.get('log'))
        start,end = snapshot.get('line_start'),snapshot.get('line_end')
        valid = (log is not None and log['sha256']==snapshot['document_sha256']
            and type(start) is int and type(end) is int and 1<=start<=end<=log['line_count'])
    result = {key: source.get(key) for key in
              ("chunk_id", "document_id", "filename", "score", "page", "paragraph")}
    result["excerpt"] = source["excerpt"] if "excerpt" in source else source.get("content", "")
    result["source_snapshot"] = snapshot if valid else None
    result["evidence_status"] = "full" if valid else "legacy_excerpt" if legacy else "snapshot_unavailable"
    if valid:
        result["filename"] = snapshot["filename"]
        if snapshot.get('source_kind')=='log':
            result.update(line_start=snapshot['line_start'],line_end=snapshot['line_end'],page=None,paragraph=None)
    return result


def _stored_document_ids(raw: str | None) -> list[int]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError) as exc:
        raise ValueError("对话的限定文档范围数据损坏") from exc
    if not isinstance(values, list) or any(
        type(value) is not int or not 0 < value <= MAX_SQLITE_ID for value in values
    ):
        raise ValueError("对话的限定文档范围数据损坏")
    return sorted(set(values))


def _scope_mode_of(document_ids: list[int] | None) -> str:
    """把文档范围列表映射成对外可见的显式模式：空 = 全库，非空 = 仅选中文档。"""
    return "selected" if document_ids else "all"


def _require_ready_documents(db: sqlite3.Connection, document_ids: list[int], user) -> None:
    if not document_ids:
        return
    # 先判可见性：不可见的文档一律按"不存在"处理（404），
    # 否则可以通过限定一个受限文档来探测它是否存在。
    allowed = acl.visible_document_ids(db, user)
    if allowed is not None and not set(document_ids) <= allowed:
        raise HTTPException(status_code=404, detail="限定文档不存在")
    placeholders = ",".join("?" * len(document_ids))
    rows = db.execute(
        f"SELECT id FROM documents WHERE status='ready' AND lifecycle='active' AND id IN ({placeholders})",
        document_ids,
    ).fetchall()
    if {int(row["id"]) for row in rows} != set(document_ids):
        raise HTTPException(
            status_code=400,
            detail="所选文档已删除、已设为历史版本或尚未处理完成，请刷新文档列表后重新选择；不会回退到全库检索",
        )


def _is_continuation(current: dict, following: dict) -> bool:
    """同一位置的下一切片只有在保留真实重叠时才算续段。"""
    if current["document_id"] != following["document_id"] \
            or following.get("seq") != current.get("seq", -2) + 1:
        return False
    same_location = current.get("page") is not None and current.get("page") == following.get("page") \
        or current.get("paragraph") is not None \
        and current.get("paragraph") == following.get("paragraph")
    if not same_location:
        return False
    left = (current.get("content") or "").rstrip()
    right = (following.get("content") or "").lstrip()
    if len(right) < 24:
        return False
    start = left.rfind(right[:24])
    overlap = left[start:] if start >= 0 else ""
    return len(overlap) >= 24 and right.startswith(overlap) and len(right) > len(overlap)


def _ranked_sources(sources: list[dict], k: int, trace: dict | None = None) -> list[dict]:
    """重排后的相关性顺序不再被旧启发式覆盖；只去同来源完全重复文本。"""
    candidates, seen, reasons = [], set(), {}
    for source in sources:
        if not source["content"].strip():
            reasons[source["chunk_id"]] = "empty_content"
            continue
        key = (source["document_id"], source.get("page"), source.get("paragraph"), source["content"])
        if key in seen:
            reasons[source["chunk_id"]] = "exact_same_source_text"
        else:
            seen.add(key)
            candidates.append(source)
    following = {(s["document_id"], s.get("seq")): s for s in candidates if isinstance(s.get("seq"), int)}
    selected, ids = [], set()
    for source in candidates:
        if len(selected) >= k:
            break
        if source["chunk_id"] in ids:
            continue
        selected.append(source)
        ids.add(source["chunk_id"])
        reasons[source["chunk_id"]] = "rerank_top_k"
        next_source = following.get((source["document_id"], source.get("seq", -2) + 1))
        if len(selected) < k and next_source and next_source["chunk_id"] not in ids and _is_continuation(source, next_source):
            selected.append(next_source)
            ids.add(next_source["chunk_id"])
            reasons[next_source["chunk_id"]] = "verified_continuation"
    if trace is not None:
        trace["selection"] = [{"chunk_id": s["chunk_id"], "selected": s["chunk_id"] in ids,
                               "reason": reasons.get(s["chunk_id"], "rank_budget")} for s in sources]
    return selected


def _question_field_values(question: str, filename: str = '') -> list[tuple[str, str]]:
    """只取明确绑定的无符号整数字面值，不截取小数、负数或型号内部数字。"""
    matches = re.findall(
        r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_]{1,31})"
        r"\s*([=:：#]|为|是|\s+)\s*([0-9]+)(?![A-Za-z0-9_.,+-])",
        question.lower(),
    )
    return list(dict.fromkeys((field,value) for field,separator,value in matches
        if not (separator.isspace() and re.search(
            r"(?<![A-Za-z0-9_.-])" + field + r"[ _-]*" + value + r"(?![A-Za-z0-9_.,+-])",
            Path(filename).stem, re.IGNORECASE))))


def _field_value_matches(content: str, wanted: list[tuple[str, str]]) -> list[str]:
    # 只认有 Format 的字段表；遇到下一字段或 Where used 就停止，不借相邻字段的编号。
    headers = list(re.finditer(
        r"(?m)^[ \t]*([A-Za-z][A-Za-z0-9_]{1,31})"
        r"(?:[ \t]+|[ \t]*\r?\n(?:(?!^[ \t]*[A-Z][A-Z0-9_]{2,31}[ \t]*$)[^:=]){0,160}?)"
        r"(?i:Format)\s*:\s*[A-Za-z]\s*[0-9]+", content,
    )) if wanted else []
    blocks = {}
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
        block = re.split(r"\bWhere\s+used\s*:", content[header.end():min(end,header.end()+4000)],
                         maxsplit=1, flags=re.IGNORECASE)[0]
        blocks.setdefault(header[1].lower(), []).append(block)
    found = []
    for field, value in wanted:
        boundary = r"(?![A-Za-z0-9_.,+-])"
        direct = re.search(
            r"(?<![A-Za-z0-9_.-])" + re.escape(field)
            + r"[ \t]*(?:[=:：#]|为|是|[ \t]+)[ \t]*" + value + boundary,
            content, re.IGNORECASE,
        )
        enum = any(re.search(r"(?m)^[ \t]*" + value + boundary
                             + r"\s*=\s*(?![0-9]+\s*=)\S", block)
                   for block in blocks.get(field, []))
        if direct or enum:
            found.append(field + '=' + value)
    return found


def _wordform_matches(candidates: list[dict], question: str) -> dict[int, list[str]]:
    """仅为原词缺失时的 -ing 词形补候选排序，不拆技术标识符或改写原文。"""
    exact = set(technical_terms(question))
    terms = {term for term in exact if re.fullmatch(r"[a-z]{3,}", term)}
    matches = {src['chunk_id']: [] for src in candidates}
    if not terms:
        return matches
    for document_id in dict.fromkeys(src['document_id'] for src in candidates):
        rows = [src for src in candidates if src['document_id'] == document_id]
        words = {src['chunk_id']: set(technical_terms(src['content'])) for src in rows}
        present = set().union(*words.values())
        title = set(technical_terms(Path(rows[0]['filename']).stem))
        retained = (exact & present) - title
        for term in sorted(terms - present - title):
            forms = {term + 'ing'}
            if term.endswith('e'):
                forms.add(term[:-1] + 'ing')
            if re.search(r"[aeiou][bcdfghjklmnpqrstvwxyz]$", term):
                forms.add(term + term[-1] + 'ing')
            for src in rows:
                if forms & words[src['chunk_id']] and retained <= words[src['chunk_id']]:
                    matches[src['chunk_id']].append(term)
    return matches


def _select_sources(sources: list[dict], k: int, question: str = "", *, preserve_ranking: bool = False,
                    trace: dict | None = None) -> list[dict]:
    """同文档的包含型重复只保留完整片段，保留不同数值/版本的原始引用。"""
    if preserve_ranking:
        return _ranked_sources(sources, k, trace)
    kept: list[tuple[dict, str]] = []
    reasons = {}
    for src in sorted(sources, key=lambda s: len(s["content"]), reverse=True):
        text = " ".join(src["content"].split())
        if text and not any(
            src["document_id"] == old["document_id"] and text in old_text
            for old, old_text in kept
        ):
            kept.append((src, text))
        else:
            reasons[src['chunk_id']] = 'contained_same_document' if text else 'empty_content'
    ids = {src["chunk_id"] for src, _ in kept}
    candidates = [src for src in sources if src["chunk_id"] in ids]
    terms = set(technical_terms(question))
    versions = {term for term in terms if re.fullmatch(r"v\d+(?:[._-]\d+)*", term)}
    models = {term for term in terms if any(c.isdigit() for c in term)} - versions
    named_terms = {term for term in terms - models - versions if len(term) >= 4}
    titles = {src["document_id"]: set(technical_terms(Path(src["filename"]).stem))
              | set(technical_terms(Path(src["filename"]).stem.replace("_", " "))) for src in candidates}
    protocols = set(re.findall(r"(?<![a-z0-9])(?:ascii|hex|secs)(?![a-z0-9])", question.lower()))
    title_protocols = {src["document_id"]: set(re.findall(
        r"(?<![a-z0-9])(?:ascii|hex|secs)(?![a-z0-9])", Path(src["filename"]).stem.lower(),
    )) for src in candidates}
    # 无文件名协议时，仅用结构化工作表标题补充协议身份；正文顺带提及不算。
    source_protocols = {}
    for src in candidates:
        matched = title_protocols[src["document_id"]]
        if not matched and Path(src["filename"]).suffix.lower() == ".xlsx":
            sheets = " ".join(re.findall(r"(?m)^工作表《([^》]+)》第\s*\d+\s*行：", src["content"]))
            explicit = set(re.findall(r"(?<![a-z0-9_])(?:ascii|hex|secs)(?![a-z0-9_])", sheets.lower()))
            if len(explicit) == 1:
                matched = explicit
        source_protocols[src["chunk_id"]] = matched
    context_titles = {doc: set(title) for doc, title in titles.items()}
    for src in candidates:
        for title in re.findall(r"(?im)^DOCUMENT TITLE:[ \t]*(?:\r?\n)?([^\r\n]+)", src["content"]):
            context_titles[src["document_id"]].update(technical_terms(title))
    strong_evidence_docs = set()
    lexical_promotions = []
    bigrams = {
        segment[i:i + 2]
        for segment in re.findall(r"[\u4e00-\u9fff]+", question)
        for i in range(len(segment) - 1)
    }
    if bigrams:
        def lexical_score(src: dict) -> int:
            content = re.sub(r"\s+", "", src["content"])
            if "工作表《" in content and Path(src["filename"]).suffix.lower() != ".xlsx":
                return 0
            return sum(term in content for term in bigrams)

        for document_id in dict.fromkeys(src["document_id"] for src in candidates):
            positions = [i for i, src in enumerate(candidates) if src["document_id"] == document_id]
            scores = [(lexical_score(candidates[i]), i) for i in positions]
            ranked = sorted(scores, key=lambda item: (-item[0], item[1]))
            leaders = [item for item in ranked if item[0] >= ranked[0][0] - 1]
            unique = ranked[0][0] >= 3 and (len(ranked) == 1 or ranked[0][0] >= ranked[1][0] + 2)
            group = (1 < len(leaders) <= k and len(leaders) < len(ranked)
                     and leaders[-1][0] >= 3 and leaders[-1][0] >= ranked[len(leaders)][0] + 2)
            if unique or group:
                winners = [ranked[0]] if unique else leaders
                exact = terms - context_titles[document_id]
                retained = exact & {term for i in positions for term in technical_terms(candidates[i]["content"])}
                if any(not retained <= set(technical_terms(candidates[i]["content"])) for _, i in winners):
                    continue
                promoted = [candidates[i] for _, i in winners]
                if unique:
                    first, best = positions[0], winners[0][1]
                    candidates[first], candidates[best] = candidates[best], candidates[first]
                else:
                    winner_ids = {source['chunk_id'] for source in promoted}
                    ordered_doc = promoted + [candidates[i] for i in positions if candidates[i]['chunk_id'] not in winner_ids]
                    for i, source in zip(positions, ordered_doc):
                        candidates[i] = source
                lexical_promotions.append({'document_id':document_id,
                    'kind':'unique_peak' if unique else 'leading_group',
                    'chunk_ids':[source['chunk_id'] for source in promoted],
                    'scores':[score for score, _ in winners]})
                strong_evidence_docs.add(document_id)
    wanted = _question_field_values(question)
    fields_by_doc = {src['document_id']: _question_field_values(question,src['filename']) for src in candidates}
    field_matches = {src['chunk_id']: _field_value_matches(src['content'], fields_by_doc[src['document_id']])
                     for src in candidates}
    word_matches = _wordform_matches(candidates, question)
    # 不抢其它文档的槽位；只在已召回的同文档候选中保留明确查询约束。
    for document_id in dict.fromkeys(src['document_id'] for src in candidates):
        positions = [i for i, src in enumerate(candidates) if src['document_id'] == document_id]
        ranked = sorted((candidates[i] for i in positions), reverse=True,
                        key=lambda src: (len(field_matches[src['chunk_id']]),len(word_matches[src['chunk_id']])))
        for i, src in zip(positions, ranked):
            candidates[i] = src
        if any(field_matches[src['chunk_id']] or word_matches[src['chunk_id']] for src in ranked):
            strong_evidence_docs.add(document_id)
    matched_docs = {doc_id for doc_id, title in titles.items() if models & title}
    # 只有一个候选文档匹配型号、且技术词均在标题中时，不用通用标题词的词法名次排其正文。
    semantic_doc = next(iter(matched_docs)) if len(matched_docs) == 1 else None
    semantic_only = semantic_doc is not None and terms <= titles[semantic_doc] \
        and semantic_doc not in strong_evidence_docs
    channels = re.findall(r"通道\s*(\d+)(?![0-9A-Za-z])(?!\s*(?:[.\-~–—]|至|到))", question)
    compact_question = re.sub(r"\s+", "", question).lower()
    first_by_doc = {}
    for src in candidates:
        first_by_doc.setdefault(src["document_id"], src["chunk_id"])

    def priority(src: dict) -> tuple:
        content = src["content"]
        channel_match = bool(channels) and all(
            re.search(r"通道\s*" + re.escape(number)
                      + r"(?![0-9A-Za-z])(?!\s*(?:[.\-~–—]|至|到))", content) for number in channels
        )
        # 必须匹配完整单元格，包括多行内容；不能把多行测试项的首行当成另一种操作。
        cells = re.findall(r"(?:^|[；：\n])[A-Z]+\d+=(.*?)(?=；[A-Z]+\d+=|\n工作表《|$)", content, re.S)
        cell_match = Path(src["filename"]).suffix.lower() == ".xlsx" and any(
            4 <= len(value) <= 80 and re.search(r"[A-Za-z]", value) and re.search(r"[\u4e00-\u9fff]", value)
            and re.sub(r"\s+", "", value).lower() in compact_question for value in cells
        )
        # 型号取文件名；协议补充只取明确且无歧义的工作表标题。
        return (len(models & titles[src["document_id"]]),
                len(protocols & source_protocols[src["chunk_id"]]),
                len(versions & titles[src["document_id"]]), channel_match, cell_match,
                bool(named_terms & titles[src["document_id"]])
                and first_by_doc[src["document_id"]] == src["chunk_id"],
                src["score"] if semantic_only and src["document_id"] == semantic_doc else 0)

    # 仅在已通过权限筛选的有界检索候选中稳定排序；同文档词法顺序不改变跨文档槽位。
    ordered = sorted(candidates, key=priority, reverse=True)
    by_sequence = {
        (src["document_id"], src.get("seq")): src for src in candidates
        if isinstance(src.get("seq"), int)
    }
    selected = []
    selected_ids = set()
    for src in ordered:
        if len(selected) >= k:
            break
        if src["chunk_id"] in selected_ids:
            continue
        selected.append(src)
        selected_ids.add(src["chunk_id"])
        reasons[src['chunk_id']] = ('field_value_match' if field_matches[src['chunk_id']]
                                    else 'query_wordform' if word_matches[src['chunk_id']] else 'rank_top_k')
        if len(selected) == k:
            break
        following = by_sequence.get((src["document_id"], src.get("seq", -2) + 1))
        if following and following["chunk_id"] not in selected_ids \
                and _is_continuation(src, following):
            selected.append(following)
            selected_ids.add(following["chunk_id"])
            reasons[following['chunk_id']] = 'verified_continuation'
            if len(selected) == k:
                break
    if trace is not None:
        ranks = {src['chunk_id']: i for i, src in enumerate(selected, 1)}
        trace['query_field_values'] = [{'field':field,'value':value} for field,value in wanted]
        trace['chinese_lexical_promotions'] = lexical_promotions
        trace['selection'] = [{'chunk_id':src['chunk_id'],'candidate_rank':i,
            'selected':src['chunk_id'] in ranks,'selection_rank':ranks.get(src['chunk_id']),
            'reason':reasons.get(src['chunk_id'],'rank_budget'),
            'field_value_matches':field_matches.get(src['chunk_id'],[]),
            'wordform_matches':word_matches.get(src['chunk_id'],[])} for i,src in enumerate(sources,1)]
    return selected


def _sources_for_chat(
    db: sqlite3.Connection,
    chat_id: int,
    allowed: set[int] | None = None,
) -> tuple[list[dict], bool]:
    """返回 (当前可见的来源, 是否存在被隐藏的来源)。

    第二个返回值用来判定"该轮是否引用了现在已不可见的文档"。仅过滤来源是不够的：
    答案正文里可能逐字引用了原文，所以调用方需要据此整轮隐藏。
    """
    rows = db.execute(
        "SELECT s.chunk_id, s.document_id, s.score, s.page, s.paragraph, s.excerpt, s.source_snapshot, d.filename "
        "FROM chat_sources s JOIN documents d ON d.id = s.document_id "
        "WHERE s.chat_id=? ORDER BY s.id",
        (chat_id,),
    ).fetchall()
    sources: list[dict] = []
    hidden = False
    for r in rows:
        # 读取历史时按**当前**权限重新判定，而不是沿用当时的授权。
        # 否则被撤销权限后，旧对话会继续把撤权资料交出去。
        if allowed is not None and int(r["document_id"]) not in allowed:
            hidden = True
            continue
        sources.append(_source_payload(dict(r)))
    return sources, hidden


def _feedback_for_chat(db: sqlite3.Connection, chat_id: int, user_id: int) -> dict | None:
    row = db.execute(
        "SELECT rating, comment, created_at FROM feedback WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def _conversation_history(
    db: sqlite3.Connection,
    conversation_id: int,
    allowed: set[int] | None,
) -> list[dict]:
    rows = db.execute(
        "SELECT id, question, answer FROM chats WHERE conversation_id=? AND status='ok' "
        "ORDER BY turn_index DESC, id DESC LIMIT ?",
        (conversation_id, HISTORY_TURNS),
    ).fetchall()
    kept: list[dict] = []
    remaining = HISTORY_CHAR_LIMIT
    for row in rows:
        # 历史答案可能逐字包含后来被撤权的文档内容，不能继续送入模型。
        _, hidden = _sources_for_chat(db, int(row["id"]), allowed)
        if hidden:
            continue
        question = (row["question"] or "").strip()
        if not question or len(question) > remaining:
            continue
        remaining -= len(question)
        kept.append({"question": question, "answer": (row["answer"] or "").strip()})
    # 先保留完整用户问题，再分配答案预算；长答不能挤掉更早的原问。
    for turn in kept:
        turn["answer"] = turn["answer"][:remaining]
        remaining -= len(turn["answer"])
    kept.reverse()
    return kept


def _canonical_followup(questions: list[str]) -> str:
    if any(not isinstance(question, str) or not question.strip() for question in questions):
        raise ValueError("关联的用户问题为空或损坏")
    return "\n".join([f"原问题：{questions[0]}"] +
                     [f"补充{index}：{question}" for index, question in enumerate(questions[1:], 1)])


def _decode_followup(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        context = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("补充关联数据损坏") from exc
    if not isinstance(context, dict) or set(context) != {"format", "parent_chat_id", "chat_ids", "effective_question"}:
        raise ValueError("补充关联格式损坏")
    ids = context["chat_ids"]
    if (type(context["format"]) is not int or context["format"] != 1
            or not isinstance(ids, list) or not 1 <= len(ids) <= FOLLOWUP_MAX_ANCESTORS
            or any(type(value) is not int or not 0 < value <= MAX_SQLITE_ID for value in ids)
            or ids != sorted(set(ids)) or type(context["parent_chat_id"]) is not int
            or context["parent_chat_id"] != ids[-1]
            or not isinstance(context["effective_question"], str)
            or not 0 < len(context["effective_question"]) <= FOLLOWUP_CHAR_LIMIT):
        raise ValueError("补充关联内容损坏")
    return context


def _followup_ancestors(db: sqlite3.Connection, record: dict, allowed: set[int] | None) -> list[dict]:
    """核对真实用户输入和整条链，不信任保存的有效问题快照。"""
    context = _decode_followup(record.get("followup_context"))
    if context is None:
        return []
    ids = context["chat_ids"]
    if ids[-1] >= record["id"]:
        raise ValueError("补充关联顺序损坏")
    ancestors = []
    for index, chat_id in enumerate(ids):
        row = db.execute(
            "SELECT * FROM chats WHERE id=? AND user_id=? AND conversation_id=? AND status='ok'",
            (chat_id, record["user_id"], record["conversation_id"]),
        ).fetchone()
        if row is None:
            raise ValueError("补充关联记录已不存在或不可用")
        ancestor = dict(row)
        _, hidden = _sources_for_chat(db, chat_id, allowed)
        if hidden:
            raise HTTPException(status_code=404, detail="补充目标不存在或不可用于本次对话")
        saved = _decode_followup(ancestor.get("followup_context"))
        if index == 0:
            if saved is not None:
                raise ValueError("补充关联链缺少原问题")
        elif saved is None or saved["chat_ids"] != ids[:index] or saved["effective_question"] != _canonical_followup(
                [item["question"] for item in ancestors] + [ancestor["question"]]):
            raise ValueError("补充关联链与原始用户问题不一致")
        ancestors.append(ancestor)
    if context["effective_question"] != _canonical_followup(
            [item["question"] for item in ancestors] + [record["question"]]):
        raise ValueError("补充关联快照与原始用户问题不一致")
    return ancestors


def _build_followup(db: sqlite3.Connection, parent_id: int, conversation_id: int, user_id: int,
                    question: str, allowed: set[int] | None) -> dict:
    row = db.execute(
        "SELECT * FROM chats WHERE id=? AND user_id=? AND conversation_id=? AND status='ok'",
        (parent_id, user_id, conversation_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="补充目标不存在或不可用于本次对话")
    parent = dict(row)
    _, hidden = _sources_for_chat(db, parent_id, allowed)
    if hidden:
        raise HTTPException(status_code=404, detail="补充目标不存在或不可用于本次对话")
    try:
        ancestors = _followup_ancestors(db, parent, allowed) + [parent]
        effective_question = _canonical_followup([item["question"] for item in ancestors] + [question])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="补充目标的关联数据已损坏，请重新选择原问题") from exc
    if len(ancestors) > FOLLOWUP_MAX_ANCESTORS:
        raise HTTPException(status_code=422, detail="补充链最多关联4条既有用户输入，请新建完整问题")
    if len(effective_question) > FOLLOWUP_CHAR_LIMIT:
        raise HTTPException(status_code=422, detail="关联后的完整问题超过2000字，请缩短补充或新建完整问题；不会截断")
    return {"format": 1, "parent_chat_id": parent_id, "chat_ids": [item["id"] for item in ancestors],
            "effective_question": effective_question}


def _history_followup(db: sqlite3.Connection, record: dict, allowed: set[int] | None) -> dict | None:
    try:
        _followup_ancestors(db, record, allowed)
        return _decode_followup(record.get("followup_context"))
    except (ValueError, HTTPException):
        return None


@router.get("/documents")
def list_query_documents(
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    rows = db.execute(
        "SELECT id, filename, version, applicability, device FROM documents WHERE status='ready' AND lifecycle='active' ORDER BY filename, id"
    ).fetchall()
    # 受限文档不出现在可选项里：否则等于告诉用户"存在一份你看不到的资料"。
    allowed = acl.visible_document_ids(db, user)
    items = [
        dict(row) for row in rows
        if allowed is None or int(row["id"]) in allowed
    ]
    return {"items": items, "total": len(items)}


@router.get('/documents/{document_id}/web-image', response_class=FileResponse)
def open_shared_web_image(document_id: int, request: Request, db: sqlite3.Connection = Depends(get_db), user=Depends(require_user), sha256: str | None = None):
    """Only published shared web images have a public-to-signed-in-users original."""
    row = db.execute("SELECT filename,stored_name,content_type,sha256,tags FROM documents "
                     "WHERE id=? AND status='ready' AND lifecycle='active' AND visibility='shared'", (document_id,)).fetchone()
    try:
        tags = json.loads(row['tags'] or '[]') if row else []
    except (TypeError, ValueError):
        tags = []
    if not row or not isinstance(tags, list) or 'web_image_shared_source' not in tags:
        raise HTTPException(status_code=404, detail='图片不存在或不可访问')
    if sha256 is not None and (not re.fullmatch(r'[0-9a-f]{64}', sha256) or sha256 != row['sha256']):
        raise HTTPException(status_code=409, detail='当前图片与回答时的来源版本不一致')
    suffix = Path(row['stored_name']).suffix.lower()
    mime = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}.get(suffix)
    if not mime or row['content_type'] != mime:
        raise HTTPException(status_code=404, detail='图片不存在或不可访问')
    root = settings.upload_dir.resolve()
    path = (root / row['stored_name']).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail='图片不存在或不可访问')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
            raise HTTPException(status_code=409, detail='图片原件内容已改变')
    audit.log_audit(db, action='document_open', user_id=user.id, username=user.username,
                    detail=f'doc:{document_id} 共享网页原图', ip=_ip(request))
    return FileResponse(path, media_type=mime, filename=row['filename'], content_disposition_type='inline',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get('/documents/{document_id}/image', response_class=FileResponse)
def open_answer_image(document_id: int, request: Request, sha256: str,
                      db: sqlite3.Connection = Depends(get_db), user=Depends(require_user)):
    """Show the cited image only while the current user can access that version."""
    allowed = acl.visible_document_ids(db, user)
    row = db.execute("SELECT filename,stored_name,content_type,sha256 FROM documents "
                     "WHERE id=? AND status='ready' AND lifecycle='active'", (document_id,)).fetchone()
    if row is None or (allowed is not None and document_id not in allowed):
        raise HTTPException(status_code=404, detail='图片不存在或不可访问')
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or sha256 != row['sha256']:
        raise HTTPException(status_code=409, detail='当前图片与回答时的来源版本不一致')
    mime = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}.get(Path(row['stored_name']).suffix.lower())
    if not mime or row['content_type'] != mime:
        raise HTTPException(status_code=404, detail='图片不存在或不可访问')
    root = settings.upload_dir.resolve()
    path = (root / row['stored_name']).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail='图片不存在或不可访问')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != row['sha256']:
            raise HTTPException(status_code=409, detail='图片原件内容已改变')
    audit.log_audit(db, action='document_open', user_id=user.id, username=user.username,
                    detail=f'doc:{document_id} 回答引用图片'
                    + (' [受限文档·管理员例外]' if allowed is None and acl.is_restricted(db, document_id) else ''),
                    ip=_ip(request))
    return FileResponse(path, media_type=mime, filename=row['filename'], content_disposition_type='inline',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.get("/documents/{document_id}/file", response_class=FileResponse)
def open_document(
    document_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_kb_admin),
):
    row = db.execute(
        "SELECT id, filename, stored_name, content_type FROM documents "
        "WHERE id=?",
        (document_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    upload_root = settings.upload_dir.resolve()
    path = (upload_root / row["stored_name"]).resolve()
    if path.parent != upload_root or not path.is_file():
        raise HTTPException(status_code=404, detail="文档文件不存在")

    audit.log_audit(
        db,
        action="document_open",
        user_id=user.id,
        username=user.username,
        # 管理员可以打开受限原文（否则无法管理这些文档），但必须留下可核查的例外记录。
        detail=f"doc:{document_id} 文件:{row['filename']}"
        + (" [受限文档·管理员例外]" if acl.is_restricted(db, document_id) else ""),
        ip=_ip(request),
    )
    return FileResponse(
        path,
        media_type=row["content_type"] or "application/octet-stream",
        filename=row["filename"],
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def _store_chat(
    db: sqlite3.Connection,
    user_id: int,
    question: str,
    answer: str,
    status: str,
    error: str | None,
    *,
    model: str | None = None,
    latency_ms: int | None = None,
    retrieval_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    sources: list[dict] | None = None,
    conversation_id: int | None = None,
    document_ids: list[int] | None = None,
    followup_context: dict | None = None,
) -> tuple[int, int]:
    """保存本轮问答与来源；整轮写在一个事务里。

    失败（例如来源约束不满足）必须整体回滚，不能留下 status=ok 但引用残缺的记录；
    自动提交连接下逐条写入会正好造成这种半成功状态。
    """
    created = now_iso()
    db.execute("BEGIN IMMEDIATE")
    try:
        if conversation_id is None:
            cur = db.execute(
                "INSERT INTO conversations (user_id, title, document_ids, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, question[:30], json.dumps(document_ids or []), created, created),
            )
            conversation_id = int(cur.lastrowid)
        turn_index = db.execute(
            "SELECT COALESCE(MAX(turn_index),0)+1 AS n FROM chats WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()["n"]
        cur = db.execute(
            "INSERT INTO chats (user_id, conversation_id, turn_index, question, answer, status, error,"
            " model, prompt_tokens, completion_tokens, latency_ms, retrieval_ms, created_at, followup_context)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id,
                conversation_id,
                turn_index,
                question,
                answer,
                status,
                error,
                model,
                prompt_tokens,
                completion_tokens,
                latency_ms,
                retrieval_ms,
                created,
                json.dumps(followup_context, ensure_ascii=False) if followup_context is not None else None,
            ),
        )
        chat_id = int(cur.lastrowid)
        db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (created, conversation_id))
        for s in _snapshot_sources(db, sources or []):
            db.execute(
                "INSERT INTO chat_sources (chat_id, document_id, chunk_id, score, page, paragraph, excerpt, source_snapshot)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    chat_id,
                    s["document_id"],
                    s.get("chunk_id"),
                    s.get("score"),
                    s.get("page"),
                    s.get("paragraph"),
                    s.get("content") or "",
                    json.dumps(s["source_snapshot"], ensure_ascii=False) if s["source_snapshot"] is not None else None,
                ),
            )
        db.commit()
        return chat_id, conversation_id
    except BaseException:
        db.rollback()
        raise


def _explicit_table_row_sources(db, question: str, document_ids: set[int], top_k: int) -> list[dict] | None:
    """完整文件名、工作表和单行地址优先定位；None 表示不是此类请求。"""
    address = re.match(r"^《([^》]+\.xlsx)》\s*的?\s*(?:工作表)?(?:《([^》]+)》|([^《》\n]+?))"
                       r"\s*第\s*([0-9]{1,7})\s*行", question.strip(), re.IGNORECASE)
    if address is None:
        return None
    # ponytail: 只处理一个明确行地址；多行比较仍走原有检索。
    if re.search(r"第\s*\d+\s*行|^[、,，至到\s-]*\d+\s*行", question.strip()[address.end():]):
        return None
    filename, quoted_sheet, sheet, row_number = address.groups()
    normalized = lambda value: " ".join(value.casefold().split())
    documents = [row[0] for row in db.execute("SELECT id,filename FROM documents WHERE status='ready' AND lifecycle='active'")
                 if row[0] in document_ids and normalized(row[1]) == normalized(filename)]
    if len(documents) != 1:
        return []
    sheet = (quoted_sheet or sheet).strip()
    marker = re.compile(r"(?:^|\n)工作表《" + re.escape(sheet) + r"》第\s*"
                        + str(int(row_number)) + r"\s*行：")
    rows = db.execute(
        "SELECT c.id AS chunk_id,c.document_id,c.seq,c.page,c.paragraph,c.content,d.filename "
        "FROM chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE c.document_id=? AND instr(c.content,?)>0 ORDER BY c.seq",
        (documents[0], f"工作表《{sheet}》"),
    )
    return [dict(record, score=1.0) for record in rows if marker.search(record['content'])][:top_k]


def _attach_table_row_continuations(db, sources: list[dict]) -> list[dict]:
    """最多补两片同一表格行的真实重叠续文，避免只看到回复头而漏掉拒绝内容。"""
    result = list(sources)
    used = {source['chunk_id'] for source in sources}
    for source in result:
        if len(result) >= len(sources) + 2:
            break
        if Path(source['filename']).suffix.lower() != '.xlsx':
            continue
        row = db.execute(
            "SELECT c.id AS chunk_id,c.document_id,c.seq,c.page,c.paragraph,c.content,d.filename "
            "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.document_id=? AND c.seq=?",
            (source['document_id'], source['seq'] + 1),
        ).fetchone()
        if row is None or row['chunk_id'] in used or re.search(r"工作表《[^》]+》第\s*\d+\s*行：", row['content']):
            continue
        following = dict(row, score=None, continuation_of=source['chunk_id'])
        if _is_continuation(source, following):
            result.append(following)
            used.add(row['chunk_id'])
    return result


def retrieve_sources(
    db: sqlite3.Connection,
    question: str,
    document_ids: set[int] | None,
    top_k: int,
    *,
    history: list[dict] | None = None,
) -> tuple[list[dict], int, str | None]:
    """检索并选出引用来源（Web 问答调用这一条路径）。

    `document_ids` 的语义与 `vector_index.search` 一致：`None` 表示不限定，
    空集合表示"没有任何可见文档"（会返回空结果而不是退化成全库）。

    返回 `(sources, retrieval_ms, empty_reason)`；`empty_reason` 为
    `"no_hits"`（向量/词法都没有命中）或 `"no_selected"`（命中但被选段阶段全部丢弃），
    以便调用方沿用各自的无结果文案。`EmbeddingUnavailable` / `RerankUnavailable` /
    `ValueError` 原样抛给调用方映射成各自协议的 503/500。
    """
    retrieval_started = time.monotonic()
    active = {row[0] for row in db.execute("SELECT id FROM documents WHERE status='ready' AND lifecycle='active'")}
    document_ids = active if document_ids is None else active & document_ids
    if not document_ids:
        return [], int((time.monotonic() - retrieval_started) * 1000), 'no_hits'
    positioned = _explicit_table_row_sources(db, question, document_ids, top_k)
    if positioned is not None:
        sources = _attach_table_row_continuations(db, positioned)
        for source in sources:
            source['table_headers'] = vector_index.table_headers_for(source['document_id'])
        return sources, int((time.monotonic() - retrieval_started) * 1000), None if sources else 'no_hits'
    retrieval_question, auxiliary_question = semantic_queries(
        question, history, settings.history_auxiliary
    )
    qvec = embedding_service.embed_query(retrieval_question)
    auxiliary_options = (
        {"auxiliary_vec": embedding_service.embed_query(auxiliary_question)}
        if auxiliary_question else {}
    )
    min_score = settings.min_relevance_score if settings.embed_backend != "mock" else None
    # 旧索引可能包含大量重叠短尾片段；有界扩大候选，去重后仍只发送 Top-K。
    hits = vector_index.search(
        qvec,
        min(200, top_k * 20),
        document_ids=document_ids,
        min_score=min_score,
        query_text=question,
        **auxiliary_options,
    )
    image_candidate = vision.candidate(db, qvec, document_ids, question, vector_index)
    if not hits and not image_candidate:
        return [], int((time.monotonic() - retrieval_started) * 1000), "no_hits"
    ids = [hit["chunk_id"] for hit in hits]
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT c.id AS chunk_id, c.document_id, c.seq, c.page, c.paragraph, c.content, d.filename "
        f"FROM chunks c JOIN documents d ON d.id = c.document_id "
        f"WHERE c.id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row["chunk_id"]: row for row in rows}
    sources: list[dict] = []
    for hit in hits:
        row = by_id.get(hit["chunk_id"])
        if row is None or row['document_id'] not in document_ids:
            continue
        sources.append(
            {
                "chunk_id": int(row["chunk_id"]),
                "document_id": int(row["document_id"]),
                "seq": int(row["seq"]),
                "filename": row["filename"],
                "page": row["page"],
                "paragraph": row["paragraph"],
                "score": round(float(hit["score"]), 4),
                "content": row["content"],
            }
        )
    if settings.rerank_enabled:
        sources = reranker.rank(question, sources)
    sources = _select_sources(
        sources, top_k, question, preserve_ranking=settings.rerank_enabled
    )
    sources = _attach_table_row_continuations(db, sources)
    if settings.evidence_completion:
        # 问题点名的字面标识符（2,1 / bit4 / Input Monitor Time 等）跨文档补入原文，
        # 保留不同定义；不改召回排序与引用编号语义。
        sources = attach_literal_evidence(db, sources, question, document_ids=document_ids)
    if settings.context_continuation:
        # 只补同文档下一段，且以原文结构为条件；来源编号/页码保持真实。
        sources = attach_continuations(db, sources)
    if any((s.get('filename') or '').endswith('.html.snapshot.txt') for s in sources):
        sources = figure_links.attach_adjacent_images(db, sources, document_ids, settings.upload_dir)
    sources = vision.attach_image(db, sources, image_candidate, document_ids)
    sources = attach_image_captions(db, sources, document_ids)
    for source in sources:
        # 组装后统一补表头元数据：新增 xlsx 来源与原有来源走同一规则。
        if Path(source["filename"]).suffix.lower() == ".xlsx":
            source["table_headers"] = vector_index.table_headers_for(source["document_id"])
    retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)
    if not sources:
        return [], retrieval_ms, "no_selected"
    return sources, retrieval_ms, None


@router.post("/query")
def query(
    body: QueryBody,
    request: Request,
    stream: bool = False,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    question = body.question.strip()
    ip = _ip(request)
    conversation_id = body.conversation_id
    requested_scope_mode = body.scope_mode
    document_ids = body.document_ids or []
    history: list[dict] = []
    followup: dict | None = None
    effective_question = question
    if conversation_id is not None:
        owned = db.execute(
            "SELECT id, document_ids FROM conversations WHERE id=? AND user_id=?",
            (conversation_id, user.id),
        ).fetchone()
        if owned is None:
            raise HTTPException(status_code=404, detail="对话不存在")
        try:
            stored_ids = _stored_document_ids(owned["document_ids"])
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        stored_mode = _scope_mode_of(stored_ids)
        # 范围在首问时固化。旧客户端省略 scope_mode 仍可追问（兼容），
        # 但一旦显式传了模式或文档列表，就必须与固化范围完全一致。
        if body.document_ids is not None and body.document_ids != stored_ids:
            raise HTTPException(status_code=409, detail="已有对话不能改变限定文档范围，请新建对话")
        if requested_scope_mode is not None and requested_scope_mode != stored_mode:
            raise HTTPException(
                status_code=409,
                detail="已有对话的检索范围已固定（全库检索 / 仅选中文档），不能改变，请新建对话",
            )
        document_ids = stored_ids
    elif requested_scope_mode == "all":
        # 显式全库：非空 document_ids 已在 QueryBody 层被拒绝，这里不再保留列表。
        document_ids = []
    elif requested_scope_mode == "selected":
        # QueryBody 已保证非空；再拦一次，避免将来放宽校验时悄悄回退到全库。
        if not document_ids:
            raise HTTPException(
                status_code=422,
                detail="仅选中文档模式必须至少选择一个文档，不能回退到全库检索",
            )
    else:
        # 兼容旧客户端：不传 scope_mode 时，空列表表示全库，非空表示限定。
        document_ids = body.document_ids or []
    _require_ready_documents(db, document_ids, user)
    # 检索范围 = 用户请求的范围 ∩ 他实际可见的范围。
    # 注意 set() 与 None 语义不同：None 表示不限定，空集合表示"没有任何可见文档"，
    # 后者会让检索直接返回空（进而拒答），而不是退化成全库检索。
    scope = acl.effective_scope(db, user, document_ids)
    if body.followup_to is not None:
        followup = _build_followup(db, body.followup_to, conversation_id, user.id, question,
                                   acl.visible_document_ids(db, user))
        effective_question = followup["effective_question"]
    elif conversation_id is not None:
        history = _conversation_history(db, conversation_id, acl.visible_document_ids(db, user))

    rt_values = rt.get_all(db)
    ok, retry = query_limiter.allow(
        key=f"user:{user.id}", limit=rt_values["queries_per_minute"]
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=(
                f"问答频率超限（每分钟 {rt_values['queries_per_minute']} 次），"
                f"请约 {int(retry) + 1} 秒后再试"
            ),
        )

    if embedding_service.state != "ready":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "embed_not_ready",
                "message": embedding_service.message or "嵌入模型尚未就绪，请稍后再试",
            },
        )

    # 检索段耗时。在 refuse 之前初始化：知识库为空/无权访问这类拒答发生在检索之前，
    # 此时它确实是 None，而不是"未测量"。
    retrieval_ms: int | None = None

    def refuse(message: str, action: str) -> dict:
        chat_id, stored_conversation_id = _store_chat(
            db, user.id, question, message, "ok", None,
            model=None, retrieval_ms=retrieval_ms,
            conversation_id=conversation_id, document_ids=document_ids,
            followup_context=followup,
        )
        audit.log_audit(
            db,
            action=action,
            user_id=user.id,
            username=user.username,
            detail=question[:200],
            ip=ip,
        )
        return {
            "answer": message,
            "chat_id": chat_id,
            "conversation_id": stored_conversation_id,
            "sources": [],
            "status": "ok",
            "retrieval_ms": retrieval_ms,
            "scope_mode": _scope_mode_of(document_ids),
            "document_ids": list(document_ids),
            "followup": followup,
        }

    if vector_index.size() == 0:
        return refuse(EMPTY_KB_ANSWER, "query_refused_empty")

    if scope == set():
        # 该账号当前没有任何可见文档。明确说明原因，而不是含糊地回一句"未找到"。
        return refuse(
            "你当前没有可访问的文档。请联系管理员为本账号开通权限。",
            "query_refused_no_access",
        )

    try:
        # 检索段计时由 retrieve_sources 内部记录；这里统一映射到 HTTP 语义。
        # 短只读快照让最终正文与文档身份处于同一数据库视图；不跨越模型生成。
        db.execute("BEGIN")
        sources, retrieval_ms, empty_reason = retrieve_sources(
            db, effective_question, scope, rt_values["top_k"], history=history
        )
        sources = _snapshot_sources(db, sources)
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "embed_not_ready", "message": str(exc)}) from exc
    except RerankUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "rerank_unavailable", "message": str(exc)}) from exc
    except ImageCaptionEvidenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if db.in_transaction:
            db.rollback()

    if empty_reason == "no_hits":
        return refuse("未找到与问题相关的可用文档，请换个问法或联系管理员。", "query_no_match")
    if empty_reason is not None:
        return refuse("知识库没有可用的检索结果，请稍后重试或联系管理员。", "query_no_match")

    doc_names = sorted({s["filename"] for s in sources})

    if stream:
        def events():
            def line(data: dict) -> str:
                return json.dumps(data, ensure_ascii=False) + "\n"

            if not llm_gate.acquire(90.0):
                yield line({"type": "error", "message": "系统繁忙：并发模型请求已达上限，请稍后重试",
                            "followup": followup, "conversation_id": conversation_id})
                return
            answer_parts: list[str] = []
            usage: dict = {}
            try:
                for event in llm_stream_chat(effective_question, sources, history):
                    if event["type"] == "delta":
                        answer_parts.append(event["text"])
                        yield line(event)
                    else:
                        usage = event
            except LLMError as exc:
                with closing(connect()) as stream_db:
                    chat_id, stored_conversation_id = _store_chat(
                        stream_db, user.id, question, "", "error", f"{exc.code}: {exc.message}",
                        model=settings.deepseek_model, retrieval_ms=retrieval_ms,
                        conversation_id=conversation_id, document_ids=document_ids,
                        followup_context=followup,
                    )
                    audit.log_audit(
                        stream_db, action="llm_query_failed", user_id=user.id, username=user.username,
                        detail=json.dumps({"question": question[:200], "documents": doc_names,
                                           "code": exc.code, "chat_id": chat_id}, ensure_ascii=False), ip=ip,
                    )
                yield line({"type": "error", "code": exc.code, "message": exc.message,
                            "conversation_id": stored_conversation_id, "followup": followup})
                return
            finally:
                llm_gate.release()

            answer = _normalize_answer("".join(answer_parts).strip())
            with closing(connect()) as stream_db:
                chat_id, stored_conversation_id = _store_chat(
                    stream_db, user.id, question, answer, "ok", None,
                    model=usage.get("model", settings.deepseek_model), latency_ms=usage["latency_ms"],
                    retrieval_ms=retrieval_ms,
                    prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
                    sources=sources, conversation_id=conversation_id, document_ids=document_ids,
                    followup_context=followup,
                )
                audit.log_audit(
                    stream_db, action="llm_query", user_id=user.id, username=user.username,
                    detail=json.dumps({"question": question[:200], "documents": doc_names,
                                       "model": usage.get("model", settings.deepseek_model),
                                       "latency_ms": usage["latency_ms"],
                                       "retrieval_ms": retrieval_ms,
                                       "prompt_tokens": usage["prompt_tokens"],
                                       "completion_tokens": usage["completion_tokens"]}, ensure_ascii=False), ip=ip,
                )
            yield line({"type": "sources", "sources": [_source_payload(source) for source in sources]})
            yield line({"type": "done", "chat_id": chat_id, "conversation_id": stored_conversation_id,
                        "latency_ms": usage["latency_ms"], "retrieval_ms": retrieval_ms,
                        "scope_mode": _scope_mode_of(document_ids),
                        "document_ids": list(document_ids), "followup": followup})

        return StreamingResponse(events(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    if not llm_gate.acquire(90.0):
        raise HTTPException(
            status_code=503,
            detail="系统繁忙：并发模型请求已达上限，请稍后重试",
        )
    try:
        try:
            result = llm_chat(effective_question, sources, history)
        except LLMError as exc:
            chat_id, stored_conversation_id = _store_chat(
                db, user.id, question, "", "error", f"{exc.code}: {exc.message}",
                model=settings.deepseek_model, retrieval_ms=retrieval_ms,
                conversation_id=conversation_id,
                document_ids=document_ids,
                followup_context=followup,
            )
            audit.log_audit(
                db,
                action="llm_query_failed",
                user_id=user.id,
                username=user.username,
                detail=json.dumps(
                    {
                        "question": question[:200],
                        "documents": doc_names,
                        "code": exc.code,
                        "chat_id": chat_id,
                    },
                    ensure_ascii=False,
                ),
                ip=ip,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "chat_id": chat_id,
                    "conversation_id": stored_conversation_id,
                    "followup": followup,
                },
            ) from exc
    finally:
        llm_gate.release()

    chat_id, stored_conversation_id = _store_chat(
        db,
        user.id,
        question,
        result["answer"],
        "ok",
        None,
        model=result["model"],
        latency_ms=result["latency_ms"],
        retrieval_ms=retrieval_ms,
        prompt_tokens=result["prompt_tokens"],
        completion_tokens=result["completion_tokens"],
        sources=sources,
        conversation_id=conversation_id,
        document_ids=document_ids,
        followup_context=followup,
    )
    audit.log_audit(
        db,
        action="llm_query",
        user_id=user.id,
        username=user.username,
        detail=json.dumps(
            {
                "question": question[:200],
                "documents": doc_names,
                "model": result["model"],
                "latency_ms": result["latency_ms"],
                "retrieval_ms": retrieval_ms,
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
            },
            ensure_ascii=False,
        ),
        ip=ip,
    )

    return {
        "answer": result["answer"],
        "chat_id": chat_id,
        "conversation_id": stored_conversation_id,
        "sources": [_source_payload(source) for source in sources],
        "status": "ok",
        # 分阶段耗时：便于前端/巡检直接看到瓶颈在哪一段，而不必去数据库里翻。
        "latency_ms": result["latency_ms"],
        "retrieval_ms": retrieval_ms,
        # 回显实际生效的范围：前端据此显示"全库检索 / 仅选中文档"，而不是自行猜测。
        "scope_mode": _scope_mode_of(document_ids),
        "document_ids": list(document_ids),
        "followup": followup,
    }


@router.get("/conversations")
def list_conversations(
    limit: int = 25,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total = db.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE user_id=?", (user.id,)
    ).fetchone()["n"]
    rows = db.execute(
        "SELECT v.id, v.title, v.created_at, v.updated_at, COUNT(c.id) AS turn_count, "
        "COALESCE(MAX(CASE WHEN c.status='error' THEN 1 ELSE 0 END),0) AS has_error "
        "FROM conversations v LEFT JOIN chats c ON c.conversation_id=v.id "
        "WHERE v.user_id=? GROUP BY v.id ORDER BY v.updated_at DESC, v.id DESC LIMIT ? OFFSET ?",
        (user.id, limit, offset),
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    conversation = db.execute(
        "SELECT v.*, u.username AS owner_username FROM conversations v "
        "JOIN users u ON u.id=v.user_id WHERE v.id=?",
        (conversation_id,),
    ).fetchone()
    if conversation is None or (user.role != "root" and conversation["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="对话不存在")
    turns = db.execute(
        "SELECT * FROM chats WHERE conversation_id=? ORDER BY turn_index, id",
        (conversation_id,),
    ).fetchall()
    item = dict(conversation)
    item["username"] = item.pop("owner_username")
    try:
        item["document_ids"] = _stored_document_ids(item.get("document_ids"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # 先按存储的真实范围判定模式，再做可见性过滤：即便部分文档已不可见，
    # 也不能把"仅选中文档"的对话显示成"全库检索"。
    item["scope_mode"] = _scope_mode_of(item["document_ids"])
    allowed = acl.visible_document_ids(db, user)
    if allowed is not None:
        # 不把已不可见的文档 id 回显给用户，避免暴露"存在一份你看不到的资料"。
        item["document_ids"] = [d for d in item["document_ids"] if d in allowed]
    item["turns"] = []
    for turn in turns:
        record = dict(turn)
        sources, hidden = _sources_for_chat(db, turn["id"], allowed)
        record["followup"] = None if hidden else _history_followup(db, record, allowed)
        record.pop("followup_context", None)
        if hidden:
            # 该轮引用的文档已不再对该账号可见。答案正文里可能逐字引用了原文，
            # 因此整轮隐藏，而不是只删掉引用来源。
            record["answer"] = "（该轮引用的文档已不再对你可见，内容已隐藏）"
            record["redacted"] = True
        record["sources"] = sources
        record["feedback"] = _feedback_for_chat(db, turn["id"], user.id)
        item["turns"].append(record)
    return item


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    conversation = db.execute(
        "SELECT v.id, v.user_id, v.title, u.username AS owner_username FROM conversations v "
        "JOIN users u ON u.id=v.user_id WHERE v.id=?",
        (conversation_id,),
    ).fetchone()
    if conversation is None or (user.role != "root" and conversation["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="对话不存在")
    with atomic(db):
        db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        audit.log_audit(
            db,
            action="conversation_delete",
            user_id=user.id,
            username=user.username,
            detail=f"conversation:{conversation_id} 所有者:{conversation['owner_username']} 标题:{conversation['title']}",
            ip=_ip(request),
        )


@router.get("/chats")
def list_chats(
    limit: int = 25,
    offset: int = 0,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    total = db.execute(
        "SELECT COUNT(*) AS n FROM chats WHERE user_id=?", (user.id,)
    ).fetchone()["n"]
    rows = db.execute(
        "SELECT id, question, answer, status, error, model, latency_ms, retrieval_ms, created_at "
        "FROM chats WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user.id, limit, offset),
    ).fetchall()
    allowed = acl.visible_document_ids(db, user)
    items = []
    for row in rows:
        item = dict(row)
        _, hidden = _sources_for_chat(db, int(row["id"]), allowed)
        if hidden:
            item["answer"] = "（该问答引用的文档已不再对你可见，内容已隐藏）"
            item["redacted"] = True
        items.append(item)
    return {"items": items, "total": total}


@router.get("/chats/{chat_id}")
def get_chat(
    chat_id: int,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    chat = db.execute(
        "SELECT c.*, u.username AS owner_username FROM chats c "
        "JOIN users u ON u.id = c.user_id WHERE c.id=?",
        (chat_id,),
    ).fetchone()
    if chat is None or (user.role != "root" and chat["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="问答记录不存在")
    item = dict(chat)
    item["username"] = item.pop("owner_username")
    allowed = acl.visible_document_ids(db, user)
    sources, hidden = _sources_for_chat(db, chat_id, allowed)
    item["followup"] = None if hidden else _history_followup(db, item, allowed)
    item.pop("followup_context", None)
    if hidden:
        # 与对话详情一致：撤权后连单条问答也不能继续交出内容。
        item["answer"] = "（该问答引用的文档已不再对你可见，内容已隐藏）"
        item["redacted"] = True
    item["sources"] = sources
    item["feedback"] = _feedback_for_chat(db, chat_id, user.id)
    return item


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(
    chat_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    chat = db.execute(
        "SELECT c.id, c.user_id, c.conversation_id, u.username AS owner_username FROM chats c "
        "JOIN users u ON u.id = c.user_id WHERE c.id=?",
        (chat_id,),
    ).fetchone()
    if chat is None or (user.role != "root" and chat["user_id"] != user.id):
        raise HTTPException(status_code=404, detail="问答记录不存在")

    with atomic(db):
        db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        latest = db.execute(
            "SELECT MAX(created_at) AS updated_at FROM chats WHERE conversation_id=?",
            (chat["conversation_id"],),
        ).fetchone()["updated_at"]
        if latest is None:
            db.execute("DELETE FROM conversations WHERE id=?", (chat["conversation_id"],))
        else:
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (latest, chat["conversation_id"]),
            )
        audit.log_audit(
            db,
            action="chat_delete",
            user_id=user.id,
            username=user.username,
            detail=f"chat:{chat_id} 所有者:{chat['owner_username']}",
            ip=_ip(request),
        )


@router.post("/chats/{chat_id}/feedback")
def save_feedback(
    chat_id: int,
    body: FeedbackBody,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    user=Depends(require_user),
):
    chat = db.execute("SELECT id FROM chats WHERE id=? AND user_id=?", (chat_id, user.id)).fetchone()
    if chat is None:
        raise HTTPException(status_code=404, detail="问答记录不存在")
    now = now_iso()
    with atomic(db):
        db.execute(
            "INSERT INTO feedback (chat_id, user_id, rating, comment, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET rating=excluded.rating, "
            "comment=excluded.comment, created_at=excluded.created_at",
            (chat_id, user.id, body.rating, body.comment, now),
        )
        audit.log_audit(
            db,
            action="feedback_submit",
            user_id=user.id,
            username=user.username,
            detail=f"chat:{chat_id} rating:{body.rating}",
            ip=_ip(request),
        )
        return {"chat_id": chat_id, "rating": body.rating, "comment": body.comment, "created_at": now}
