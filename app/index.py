"""内存向量索引：入库/删除/重建后从 SQLite 全量重建，NumPy 点积 Top-K。

数据规模目标：约 5 万切片（512 维 float32 ≈ 100MB 内存），超出后迁移
PostgreSQL + pgvector。
"""
from __future__ import annotations

import sqlite3
import threading

import numpy as np

from .config import settings


class VectorIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunk_ids = np.empty(0, dtype=np.int64)
        self._document_ids = np.empty(0, dtype=np.int64)
        self._vectors = np.empty((0, 0), dtype=np.float32)

    def reload(self, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "SELECT c.id AS chunk_id, c.document_id, c.vector "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.status = 'ready' ORDER BY c.id"
        ).fetchall()
        chunk_ids: list[int] = []
        doc_ids: list[int] = []
        vecs: list[np.ndarray] = []
        dim: int | None = None
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
        with self._lock:
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
    ) -> list[dict]:
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        with self._lock:
            vecs = self._vectors
            cids = self._chunk_ids
            dids = self._document_ids
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
        if min_score is not None:
            score_mask = scores >= float(min_score)
            vecs = vecs[score_mask]
            cids = cids[score_mask]
            dids = dids[score_mask]
            scores = scores[score_mask]
            n = scores.shape[0]
            if n == 0:
                return []
        k = min(int(k), n)
        if k <= 0:
            return []
        if k == n:
            order = np.argsort(-scores)
        else:
            top = np.argpartition(-scores, k - 1)[:k]
            order = top[np.argsort(-scores[top])]
        return [
            {
                "chunk_id": int(cids[i]),
                "document_id": int(dids[i]),
                "score": float(scores[i]),
            }
            for i in order
        ]


vector_index = VectorIndex()
