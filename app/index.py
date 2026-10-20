"""内存检索索引：NumPy 语义检索 + SQLite FTS5 精确技术标识符检索。

数据规模目标：约 5 万切片（512 维 float32 ≈ 100MB 内存），超出后迁移
PostgreSQL + pgvector。
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from .config import settings


def technical_terms(text: str) -> list[str]:
    """提取完整 ASCII 标识符；中文紧邻 P20 时仍可识别，不把 P200 当 P20。"""
    # PDF 可把大写结构化标识符的首字母拆成独立行；只补别名，不改正文或原词。
    aliases = re.findall(r"\b([A-Z])[ \t]*\r?\n[ \t]*([A-Z0-9]+(?:_[A-Z0-9]+)+)\b", text)
    terms = re.findall(r"[a-z][a-z0-9]*(?:[_.-][a-z0-9]+)*", text.lower())
    terms.extend((head + tail).lower() for head, tail in aliases)
    return list(dict.fromkeys(terms))


def lexical_terms(text: str, *, query: bool = False) -> list[str]:
    """通用词项独立命名空间：汉字二字片段、单字回退和完整数字表达。"""
    terms = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.extend("z2" + run[i:i + 2].encode("utf-8").hex() for i in range(len(run) - 1))
        if not query or len(run) == 1:
            terms.extend("z1" + char.encode("utf-8").hex() for char in run)
    # 不从小数、科学计数、型号、版本或连字符编码内部截取整数。
    numbers = re.findall(r"(?<![A-Za-z0-9_.+-])[+-]?[0-9]+(?:[.,][0-9]+)?(?:[eE][+-]?[0-9]+)?"
                         r"(?![A-Za-z0-9_.+-])", text)
    terms.extend("n" + value.lower().encode("ascii").hex() for value in numbers)
    return list(dict.fromkeys(terms))


def semantic_queries(question: str, history: list[dict], auxiliary: bool) -> tuple[str, str | None]:
    """只使用上一轮用户问题；历史答案绝不参与检索。关闭时保留旧对照路径。"""
    previous = history[-1]["question"] if history else None
    combined = f"{previous}\n{question}" if previous else question
    return (question, combined if previous else None) if auxiliary else (combined, None)


class VectorIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunk_ids = np.empty(0, dtype=np.int64)
        self._document_ids = np.empty(0, dtype=np.int64)
        self._vectors = np.empty((0, 0), dtype=np.float32)
        self._keywords: sqlite3.Connection | None = None
        self._filenames: dict[int, str] = {}
        self._filename_terms: dict[int, set[str]] = {}
        self._table_headers: dict[int, dict[str, dict]] = {}

    def reload(self, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "SELECT c.id AS chunk_id, c.document_id, c.vector, c.content, d.filename "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.status = 'ready' ORDER BY c.id"
        ).fetchall()
        chunk_ids: list[int] = []
        doc_ids: list[int] = []
        vecs: list[np.ndarray] = []
        dim: int | None = None
        keyword_rows = []
        general_rows = []
        filenames: dict[int, str] = {}
        filename_terms: dict[int, set[str]] = {}
        table_headers: dict[int, dict[str, dict]] = {}
        header_roles = {"type", "input", "precondition", "测试项", "前置条件", "测试步骤", "测试步骤-输入", "期望回复"}
        secs_gap = r"[ \t]*(?:\r?\n[ \t]*)?"
        # ponytail: 只认明确逗号分隔的正文编号；数字自身拆行时跳过，不猜短编号。
        secs_headers = re.compile(
            r"(?<![A-Za-z0-9_.-])S" + secs_gap + r"([0-9]{1,3})" + secs_gap
            + "," + secs_gap + "F" + secs_gap + r"([0-9]{1,3})"
            + r"(?![A-Za-z0-9_.-]|\s*[0-9])"
        )
        for r in rows:
            try:
                v = np.frombuffer(r["vector"], dtype=np.float32)
            except (TypeError, ValueError):
                continue  # 跳过损坏的向量 BLOB，避免单条脏数据阻断启动
            if v.size == 0:
                continue
            if dim is None:
                dim = v.shape[0]
            elif v.shape[0] != dim:
                continue  # 跳过异常脏数据（正常流程不会出现）
            chunk_ids.append(int(r["chunk_id"]))
            doc_ids.append(int(r["document_id"]))
            vecs.append(v)
            doc_id = int(r["document_id"])
            filenames[doc_id] = " ".join(r["filename"].casefold().split())
            if doc_id not in filename_terms:
                filename_terms[doc_id] = set(technical_terms(Path(r["filename"]).stem))
            if Path(r["filename"]).suffix.lower() == ".xlsx":
                for sheet, row in re.findall(
                    r"工作表《([^》]+)》第\s*1\s*行：(.*?)(?=\n工作表《[^》]+》第\s*\d+\s*行：|$)",
                    r["content"], re.S,
                ):
                    columns = {column: value.strip() for column, value in
                               re.findall(r"(?:^|；)([A-Z]+)1=(.*?)(?=；[A-Z]+1=|$)", row, re.S)
                               if 0 < len(value.strip()) <= 40 and "\n" not in value.strip()
                               and "\r" not in value.strip()}
                    # 首行也可能是标题或数据；至少两个明示字段角色才确认，未知表保持原文。
                    if sum(re.sub(r"\s+", "", label).lower() in header_roles for label in columns.values()) >= 2:
                        table_headers.setdefault(doc_id, {}).setdefault(
                            sheet, {"chunk_id": int(r["chunk_id"]), "columns": columns})
            # 标识符编码为单个 FTS token，保留点/下划线/连字符，避免混淆命令名。
            content_terms = technical_terms(r["content"])
            content_terms.extend(f"s{stream}f{function}" for stream, function
                                 in secs_headers.findall(r["content"]))
            # 保留词法别名只供 E84 日志的 TP 家族查询使用，不把 TPLink 当作计时器。
            if any(re.fullmatch(r"tp\d*", term) for term in content_terms):
                content_terms.append("tp#timer")
            # 只扩展正文倒排项，不扩展查询：多个别名不能绕过单一宽泛型号的保护。
            content_terms.extend(part for term in list(content_terms)
                                 if re.fullmatch(r"[a-z]+(?:-[a-z]+)+", term)
                                 for part in term.split("-"))
            terms = " ".join(term.encode("ascii").hex() for term in dict.fromkeys(content_terms))
            keyword_rows.append((int(r["chunk_id"]), int(r["document_id"]), terms))
            general_rows.append((int(r["chunk_id"]), int(r["document_id"]), " ".join(lexical_terms(r["content"]))))
        keywords = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            keywords.execute("CREATE VIRTUAL TABLE terms USING fts5(document_id UNINDEXED, tokens)")
            keywords.executemany("INSERT INTO terms(rowid,document_id,tokens) VALUES (?,?,?)", keyword_rows)
            keywords.execute("CREATE VIRTUAL TABLE general_terms USING fts5(document_id UNINDEXED, tokens)")
            keywords.executemany("INSERT INTO general_terms(rowid,document_id,tokens) VALUES (?,?,?)", general_rows)
            keywords.commit()
        except Exception:
            keywords.close()
            raise
        with self._lock:
            previous_keywords = self._keywords
            self._keywords = keywords
            self._filenames = filenames
            self._filename_terms = filename_terms
            self._table_headers = table_headers
            if previous_keywords is not None:
                previous_keywords.close()
            if not vecs:
                self._chunk_ids = np.empty(0, dtype=np.int64)
                self._document_ids = np.empty(0, dtype=np.int64)
                self._vectors = np.empty((0, 0), dtype=np.float32)
            else:
                self._chunk_ids = np.asarray(chunk_ids, dtype=np.int64)
                self._document_ids = np.asarray(doc_ids, dtype=np.int64)
                self._vectors = np.stack(vecs).astype(np.float32)

    def size(self) -> int:
        with self._lock:
            return int(self._vectors.shape[0])

    def table_headers_for(self, document_id: int) -> dict[str, dict]:
        """取同一 ready 文档的工作表表头；返回副本，避免调用方修改共享索引。"""
        with self._lock:
            return {sheet: {"chunk_id": header["chunk_id"], "columns": dict(header["columns"])}
                    for sheet, header in self._table_headers.get(document_id, {}).items()}

    def dim(self) -> int:
        with self._lock:
            return int(self._vectors.shape[1]) if self._vectors.size else int(settings.embed_dim)

    def _keyword_exists(self, term: str, document_ids: set[int] | None) -> bool:
        """该技术标识符在当前文档范围内是否存在倒排项（决定它该不该参与 AND）。

        术语总数有上限（见 search 里的 16），每个只查一次内存 FTS 表，成本可忽略。
        """
        if self._keywords is None:  # pragma: no cover - search 已先行判断
            return False
        sql = "SELECT 1 FROM terms WHERE terms MATCH ?"
        params: list = ['"' + term.encode("ascii").hex() + '"']
        if document_ids is not None:
            sql += " AND document_id IN (" + ",".join("?" for _ in document_ids) + ")"
            params.extend(sorted(document_ids))
        row = self._keywords.execute(sql + " LIMIT 1", params).fetchone()
        return row is not None

    def _filename_scope(self, query_text: str, document_ids: set[int] | None) -> set[int] | None:
        """完整文件名约束两路检索；不猜缩写，也不把否定提及当正向限定。"""
        query = " ".join(query_text.casefold().split())
        extension = r"\.(?:pdf|docx?|xlsx|txt|md)"
        filename = r'[^=《》“”"`，,；;：:?？\n]+?' + extension
        # ponytail: 只识别明示文档引用，不解析任意自然语言文件别名或否定目标。
        # 语法独立于全库名称，避免通过不可见文件的有无探测其存在性。
        references = (r"《(" + filename + r")》|"
                      r'(?:(?:仅)?(?:根据|依据|按|在|参考|对照|与|和|及)\s*)[《“"`]?('
                      + filename + r')[》”"`]?(?![a-z0-9_.-])|'
                      r'^[“"`](' + filename + r')[”"`](?=\s*(?:中|里|内|的|与|和|及))|'
                      r"^([a-z0-9][^=《》“”\"`，,；;：:?？\n]*?" + extension
                      + r")(?=\s*(?:中|里|内|的|与|和|及))")
        mentioned = set()
        for match in re.finditer(references, query):
            before = query[:match.start()]
            after = query[match.end():]
            prefix = re.split(r"[，,；;。！？?!]", before)[-1] + match.group()
            if re.search(r"(?:文件名|路径|示例字符串)\s*(?:设(?:置)?为|是|为|=)|(?:保存|另存|命名)为", prefix) \
                    or re.match(r"\s*这个字符串", after):
                continue  # 输出文件名/字符串数据不是被查询的来源文档。
            if re.search(r"(?:不|勿|别|不是|不要|排除|除了|而非|\bnot)(?:按|用|依据|参考|根据|对照|以)?\s*$", before) \
                    or re.match(r"\s*(?:不要(?:按|用|参考|根据|依据)|别(?:按|用|参考)|不(?:用|作为|参考)|除外)", after):
                continue
            mentioned.add(next(value for value in match.groups() if value).strip())
        if not mentioned:
            return document_ids
        names = {name for doc, name in self._filenames.items() if document_ids is None or doc in document_ids}
        if not mentioned <= names:
            return set()  # 明示缺失文件不能悄悄替换成相似文档。
        matched = {doc for doc, name in self._filenames.items() if name in mentioned}
        return matched if document_ids is None else matched & document_ids

    def search(
        self,
        query_vec: np.ndarray,
        k: int,
        document_ids: set[int] | None = None,
        min_score: float | None = None,
        query_text: str = "",
        trace: dict | None = None,
        general_lexical: bool | None = None,
        auxiliary_vec: np.ndarray | None = None,
    ) -> list[dict]:
        started = time.perf_counter()
        if trace is not None:
            trace.update(channels={}, fused=[], timings_ms={}, scope=[])
        if k <= 0 or document_ids == set():
            return []
        result_limit = int(k)
        use_general = settings.general_lexical if general_lexical is None else general_lexical
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        terms = technical_terms(query_text)
        e84_log_intent = "日志" in query_text and "e84" in terms
        if e84_log_intent:
            terms = ["tp#timer" if term == "tp" else term for term in terms]
            if "log" not in terms:
                terms.append("log")
        keyword_ids = []
        general_ids = []
        general_scores = {}
        with self._lock:
            document_ids = self._filename_scope(query_text, document_ids)
            if trace is not None:
                trace["scope"] = sorted(document_ids) if document_ids is not None else None
            if document_ids == set():
                return []
            vecs = self._vectors
            cids = self._chunk_ids
            dids = self._document_ids
            general_started = time.perf_counter()
            if use_general and self._keywords is not None:
                # ponytail: 查询最多256个去重词项；不建立事件/参数目录，不改变原切片。
                tokens = lexical_terms(query_text, query=True)[:256]
                if tokens:
                    sql = "SELECT rowid, rank FROM general_terms WHERE general_terms MATCH ?"
                    params = [" OR ".join('"' + token + '"' for token in tokens)]
                    if document_ids is not None:
                        sql += " AND document_id IN (" + ",".join("?" for _ in document_ids) + ")"
                        params.extend(sorted(document_ids))
                    sql += " ORDER BY rank,rowid LIMIT ?"
                    params.append(min(200, result_limit))
                    general_rows = list(self._keywords.execute(sql, params))
                    general_ids = [row[0] for row in general_rows]
                    if trace is not None:
                        general_scores = {int(cid): float(score) for cid, score in general_rows}
            if trace is not None:
                trace["timings_ms"]["general_lexical"] = (time.perf_counter() - general_started) * 1000
            technical_started = time.perf_counter()
            # 仅有界召回精确技术标识符；普通中文问题保持原语义检索。
            if self._keywords is not None and 0 < len(terms) <= 16:
                # 先剔除"在当前文档范围内任何切片里都不存在"的术语，再做 AND。
                # 一个零倒排项的术语与其它术语做 AND 不可能提高精度，只会把整个通道打成空，
                # 而且完全静默。实测（真实语料）：问题「Fortrend LP PxM 软件里，如何设置
                # 设备的通讯参数？」提取出 fortrend/lp/pxm，而手册正文写的是 LP/PLM、LP-150，
                # 从没有 PxM（PxM 只在文件名里）——于是 AND 命中 0 个切片，精确匹配整个失效，
                # 只剩向量检索，结果答成了 PLM2.0/Plus Pro 的内容。
                live_terms = [term for term in terms if self._keyword_exists(term, document_ids)]
                broad_filename_query = bool(live_terms) and sum(
                    all(term in title for term in live_terms)
                    for doc_id, title in self._filename_terms.items()
                    if document_ids is None or doc_id in document_ids
                ) > 1
                # 一个或多个查询词只共同表示多文件产品族时，不让封面正文压过语义结果。
                if live_terms and not broad_filename_query:
                    match = " AND ".join(
                        '"' + term.encode("ascii").hex() + '"' for term in live_terms
                    )
                    sql = "SELECT rowid FROM terms WHERE terms MATCH ?"
                    params: list = [match]
                    if document_ids is not None:
                        sql += " AND document_id IN (" + ",".join("?" for _ in document_ids) + ")"
                        params.extend(sorted(document_ids))
                    sql += " ORDER BY rank, rowid LIMIT ?"
                    params.append(min(200, int(k)))
                    keyword_ids = [row[0] for row in self._keywords.execute(sql, params)]
                title_only = {
                    term for term in terms if term not in live_terms
                    and any(term in title for doc_id, title in self._filename_terms.items()
                            if document_ids is None or doc_id in document_ids)
                }
                if e84_log_intent:
                    title_only.update(("e84", "log"))
                if (not broad_filename_query and not keyword_ids and len(live_terms) >= 2) or title_only:
                    # 文件名里的术语可限定文档，不要求每个正文切片也重复该术语。
                    # 标题独有术语即使被正文预筛剔除，也不能让其它文档的 AND 命中盖过它。
                    # 仍需至少一个正文术语；纯文件名查询不走这条精确匹配回退。
                    fallback = []
                    groups: dict[tuple[str, ...], list[int]] = {}
                    for doc_id, title_terms in self._filename_terms.items():
                        if document_ids is not None and doc_id not in document_ids:
                            continue
                        if title_only and not title_only.issubset(title_terms):
                            continue
                        required = tuple(term for term in live_terms if term not in title_terms)
                        if not required or (len(required) == len(live_terms) and not title_only):
                            continue
                        groups.setdefault(required, []).append(doc_id)
                    for required, doc_ids in groups.items():
                        match = " AND ".join(
                            '"' + term.encode("ascii").hex() + '"' for term in required
                        )
                        rows = self._keywords.execute(
                            "SELECT rowid, rank FROM terms WHERE terms MATCH ? AND document_id IN ("
                            + ",".join("?" for _ in doc_ids) + ") "
                            "ORDER BY rank, rowid LIMIT ?",
                            (match, *doc_ids, min(200, int(k))),
                        )
                        fallback.extend((len(required), float(rank), int(cid))
                                        for cid, rank in rows)
                    # 先按该文档正文必须命中的术语数排序，因此文件名覆盖越多 live_terms 的文档越靠后；
                    # 该键同时决定候选截断优先级。不同 MATCH 的 BM25 rank 不可比，
                    # 同数量时仅把 rank 当作启发式次序，不能视为校准后的相关性分数。
                    fallback.sort(key=lambda hit: (-hit[0], hit[1], hit[2]))
                    if fallback:
                        # 在合格标题范围内保留完整正文 AND，避免更宽的回退覆盖强证据。
                        # 重新限定查询，不能依赖已被全局排名或回退 LIMIT 截断的候选。
                        eligible_ids = sorted({doc_id for doc_ids in groups.values() for doc_id in doc_ids})
                        match = " AND ".join(
                            '"' + term.encode("ascii").hex() + '"' for term in live_terms
                        )
                        rows = self._keywords.execute(
                            "SELECT rowid FROM terms WHERE terms MATCH ? AND document_id IN ("
                            + ",".join("?" for _ in eligible_ids) + ") "
                            "ORDER BY rank, rowid LIMIT ?",
                            (match, *eligible_ids, min(200, int(k))),
                        )
                        keyword_ids = list(dict.fromkeys(
                            [row[0] for row in rows] + [cid for _, _, cid in fallback]
                        ))[:min(200, int(k))]
            if trace is not None:
                trace["timings_ms"]["technical"] = (time.perf_counter() - technical_started) * 1000
        semantic_started = time.perf_counter()
        if document_ids is not None:
            mask = np.asarray([int(doc_id) in document_ids for doc_id in dids], dtype=bool)
            vecs = vecs[mask]
            cids = cids[mask]
            dids = dids[mask]
        n = vecs.shape[0]
        if n == 0:
            return []
        if q.shape[0] != vecs.shape[1]:
            raise ValueError(
                f"查询向量维度 {q.shape[0]} 与知识库索引维度 {vecs.shape[1]} 不一致"
            )
        scores = vecs @ q
        all_scores = scores
        all_vectors = vecs
        all_cids, all_dids = cids, dids
        if min_score is not None:
            score_mask = scores >= float(min_score)
            vecs = vecs[score_mask]
            cids = cids[score_mask]
            dids = dids[score_mask]
            scores = scores[score_mask]
            n = scores.shape[0]
            if n == 0 and not keyword_ids and not general_ids and auxiliary_vec is None:
                return []
        k = min(int(k), n)
        if k == n:
            order = np.argsort(-scores)
        else:
            top = np.argpartition(-scores, k - 1)[:k]
            order = top[np.argsort(-scores[top])]
        semantic = [
            {
                "chunk_id": int(cids[i]),
                "document_id": int(dids[i]),
                "score": float(scores[i]),
            }
            for i in order
        ]
        positions = {int(cid): i for i, cid in enumerate(all_cids)}
        current_ids = [hit["chunk_id"] for hit in semantic]
        history_ids = []
        if auxiliary_vec is not None:
            auxiliary = np.asarray(auxiliary_vec, dtype=np.float32).reshape(-1)
            if auxiliary.shape != q.shape:
                raise ValueError("历史辅助查询向量维度与当前问题不一致")
            auxiliary_scores = all_vectors @ auxiliary
            eligible = np.where(auxiliary_scores >= float(min_score))[0] if min_score is not None else np.arange(len(all_cids))
            order = sorted(eligible, key=lambda i: (-float(auxiliary_scores[i]), int(all_cids[i])))[:result_limit]
            history_ids = [int(all_cids[i]) for i in order]
            # 两个语义通道先2:1融合成一个排名，再与词法通道融合一次。
            semantic_rrf = {}
            for ranked, weight in ((current_ids, 2), (history_ids, 1)):
                for rank, cid in enumerate(ranked, 1):
                    semantic_rrf[cid] = semantic_rrf.get(cid, 0.0) + weight / (60 + rank)
            current_positions = {cid: rank for rank, cid in enumerate(current_ids)}
            combined = sorted(semantic_rrf, key=lambda cid: (-semantic_rrf[cid], current_positions.get(cid, float("inf")), cid))[:result_limit]
            semantic = [{"chunk_id": cid, "document_id": int(all_dids[positions[cid]]),
                         "score": float(all_scores[positions[cid]])} for cid in combined]
        keyword_ids = [cid for cid in keyword_ids if cid in positions]
        general_ids = [cid for cid in general_ids if cid in positions]
        if trace is not None:
            trace["channels"] = {"technical": list(keyword_ids), "semantic": [hit["chunk_id"] for hit in semantic]}
            trace["channels"]["general"] = list(general_ids)
            trace["channels"].update(semantic_current=current_ids, semantic_history=history_ids)
            trace["semantic_weights"] = [2, 1] if auxiliary_vec is not None else [1, 0]
            trace["timings_ms"]["semantic"] = (time.perf_counter() - semantic_started) * 1000
            trace["rankings"] = {name: [{"chunk_id": cid, "rank": rank} for rank, cid in enumerate(ids, 1)]
                                 for name, ids in trace["channels"].items()}
            for hit in trace["rankings"]["general"]:
                hit["bm25"] = general_scores[hit["chunk_id"]]
            for name in ("semantic_current", "semantic"):
                for hit in trace["rankings"][name]:
                    hit["current_cosine"] = float(all_scores[positions[hit["chunk_id"]]])
            if auxiliary_vec is not None:
                for hit in trace["rankings"]["semantic_history"]:
                    hit["auxiliary_cosine"] = float(auxiliary_scores[positions[hit["chunk_id"]]])
                trace["semantic_rrf"] = [{"chunk_id": cid, "rrf": semantic_rrf[cid]} for cid in combined]
            trace["timings_ms"]["recall"] = (time.perf_counter() - started) * 1000
        if not keyword_ids and not general_ids:
            if trace is not None:
                trace["fused"] = [hit["chunk_id"] for hit in semantic]
                trace["timings_ms"]["search_total"] = (time.perf_counter() - started) * 1000
            return semantic
        # 按倒数排名融合，不把 BM25 和余弦直接相加；来源仍记录原始余弦分数。
        fused: dict[int, float] = {}
        for ranked in ([hit["chunk_id"] for hit in semantic], keyword_ids, general_ids):
            for rank, cid in enumerate(ranked, 1):
                fused[cid] = fused.get(cid, 0.0) + 1 / (60 + rank)
        keyword_set = set(keyword_ids)
        ranked_ids = sorted(fused, key=lambda cid: (-fused[cid], cid not in keyword_set, cid))
        if trace is not None:
            trace["fused"] = ranked_ids[:result_limit]
            trace["fusion_scores"] = [{"chunk_id": cid, "rrf": fused[cid]} for cid in ranked_ids[:result_limit]]
            trace["timings_ms"]["search_total"] = (time.perf_counter() - started) * 1000
        return [{"chunk_id": cid, "document_id": int(all_dids[positions[cid]]),
                 "score": float(all_scores[positions[cid]])}
                for cid in ranked_ids[:result_limit]]


vector_index = VectorIndex()
