"""内存检索索引：NumPy 语义检索 + SQLite FTS5 精确技术标识符检索。

数据规模目标：约 5 万切片（512 维 float32 ≈ 100MB 内存），超出后迁移
PostgreSQL + pgvector。
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

import numpy as np

from .config import settings


def technical_terms(text: str) -> list[str]:
    """提取完整 ASCII 标识符；中文紧邻 P20 时仍可识别，不把 P200 当 P20。"""
    return list(dict.fromkeys(re.findall(r"[a-z][a-z0-9]*(?:[_.-][a-z0-9]+)*", text.lower())))


class VectorIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunk_ids = np.empty(0, dtype=np.int64)
        self._document_ids = np.empty(0, dtype=np.int64)
        self._vectors = np.empty((0, 0), dtype=np.float32)
        self._keywords: sqlite3.Connection | None = None
        self._filename_terms: dict[int, set[str]] = {}

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
        filename_terms: dict[int, set[str]] = {}
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
            if doc_id not in filename_terms:
                filename_terms[doc_id] = set(technical_terms(Path(r["filename"]).stem))
            # 标识符编码为单个 FTS token，保留点/下划线/连字符，避免混淆命令名。
            terms = " ".join(term.encode("ascii").hex() for term in technical_terms(r["content"]))
            keyword_rows.append((int(r["chunk_id"]), int(r["document_id"]), terms))
        keywords = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            keywords.execute("CREATE VIRTUAL TABLE terms USING fts5(document_id UNINDEXED, tokens)")
            keywords.executemany("INSERT INTO terms(rowid,document_id,tokens) VALUES (?,?,?)", keyword_rows)
            keywords.commit()
        except Exception:
            keywords.close()
            raise
        with self._lock:
            previous_keywords = self._keywords
            self._keywords = keywords
            self._filename_terms = filename_terms
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

    def search(
        self,
        query_vec: np.ndarray,
        k: int,
        document_ids: set[int] | None = None,
        min_score: float | None = None,
        query_text: str = "",
    ) -> list[dict]:
        if k <= 0 or document_ids == set():
            return []
        result_limit = int(k)
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        terms = technical_terms(query_text)
        keyword_ids = []
        with self._lock:
            vecs = self._vectors
            cids = self._chunk_ids
            dids = self._document_ids
            # 仅有界召回精确技术标识符；普通中文问题保持原语义检索。
            if self._keywords is not None and 0 < len(terms) <= 16:
                # 先剔除"在当前文档范围内任何切片里都不存在"的术语，再做 AND。
                # 一个零倒排项的术语与其它术语做 AND 不可能提高精度，只会把整个通道打成空，
                # 而且完全静默。实测（真实语料）：问题「Fortrend LP PxM 软件里，如何设置
                # 设备的通讯参数？」提取出 fortrend/lp/pxm，而手册正文写的是 LP/PLM、LP-150，
                # 从没有 PxM（PxM 只在文件名里）——于是 AND 命中 0 个切片，精确匹配整个失效，
                # 只剩向量检索，结果答成了 PLM2.0/Plus Pro 的内容。
                live_terms = [term for term in terms if self._keyword_exists(term, document_ids)]
                if live_terms:
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
                if (not keyword_ids and len(live_terms) >= 2) or title_only:
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
                    # 先按该文档正文必须命中的术语数排序，因此文件名覆盖越多术语的文档越靠后；
                    # 该键同时决定候选截断优先级。不同 MATCH 的 BM25 rank 不可比，
                    # 同数量时仅把 rank 当作启发式次序，不能视为校准后的相关性分数。
                    fallback.sort(key=lambda hit: (-hit[0], hit[1], hit[2]))
                    if fallback:
                        keyword_ids = [cid for _, _, cid in fallback[:min(200, int(k))]]
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
        all_cids, all_dids = cids, dids
        if min_score is not None:
            score_mask = scores >= float(min_score)
            vecs = vecs[score_mask]
            cids = cids[score_mask]
            dids = dids[score_mask]
            scores = scores[score_mask]
            n = scores.shape[0]
            if n == 0 and not keyword_ids:
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
        if not keyword_ids:
            return semantic
        # 按倒数排名融合，不把 BM25 和余弦直接相加；来源仍记录原始余弦分数。
        fused: dict[int, float] = {}
        for ranked in ([hit["chunk_id"] for hit in semantic], keyword_ids):
            for rank, cid in enumerate(ranked, 1):
                fused[cid] = fused.get(cid, 0.0) + 1 / (60 + rank)
        positions = {int(cid): i for i, cid in enumerate(all_cids)}
        keyword_set = set(keyword_ids)
        ranked_ids = sorted(fused, key=lambda cid: (-fused[cid], cid not in keyword_set, cid))
        return [{"chunk_id": cid, "document_id": int(all_dids[positions[cid]]),
                 "score": float(all_scores[positions[cid]])}
                for cid in ranked_ids[:result_limit]]


vector_index = VectorIndex()
