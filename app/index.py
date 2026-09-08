"""内存检索索引：NumPy 语义检索 + SQLite FTS5 精确技术标识符检索。

数据规模目标：约 5 万切片（512 维 float32 ≈ 100MB 内存），超出后迁移
PostgreSQL + pgvector。
"""
from __future__ import annotations

import re
import sqlite3
import threading

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

    def reload(self, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "SELECT c.id AS chunk_id, c.document_id, c.vector, c.content "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.status = 'ready' ORDER BY c.id"
        ).fetchall()
        chunk_ids: list[int] = []
        doc_ids: list[int] = []
        vecs: list[np.ndarray] = []
        dim: int | None = None
        keyword_rows = []
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
                match = " AND ".join('"' + term.encode("ascii").hex() + '"' for term in terms)
                sql = "SELECT rowid FROM terms WHERE terms MATCH ?"
                params: list = [match]
                if document_ids is not None:
                    sql += " AND document_id IN (" + ",".join("?" for _ in document_ids) + ")"
                    params.extend(sorted(document_ids))
                sql += " ORDER BY rank, rowid LIMIT ?"
                params.append(min(200, int(k)))
                keyword_ids = [row[0] for row in self._keywords.execute(sql, params)]
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
