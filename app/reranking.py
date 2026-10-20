"""Optional local CPU CrossEncoder; window scores never replace original evidence."""
from __future__ import annotations

import math
from pathlib import Path
import threading
import time

from .config import settings


class RerankUnavailable(RuntimeError):
    pass


class Reranker:
    WINDOW = 512
    BATCH = 4

    def __init__(self):
        self._lock = threading.RLock()
        self._model = None
        self.state = "not_loaded"
        self.message = "重排模型未加载"

    def start(self):
        if settings.rerank_enabled:
            threading.Thread(target=self._background_load, name="rerank-loader", daemon=True).start()

    def _background_load(self):
        try:
            self._load()
        except RerankUnavailable:
            pass  # /ready and query report the explicit failure; never claim a fallback succeeded.

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            self.state = "loading"
            try:
                path = Path(settings.rerank_model)
                if not path.is_dir():
                    raise FileNotFoundError
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(str(path), device="cpu", max_length=self.WINDOW,
                                           local_files_only=True, trust_remote_code=False)
            except Exception as exc:
                self.state = "error"
                self.message = f"重排已启用但本地模型不可用（{type(exc).__name__}）；请准备 BAAI/bge-reranker-v2-m3，未静默回退。"
                raise RerankUnavailable(self.message) from exc
            self.state, self.message = "ready", "本地 CPU 重排就绪，batch=4，window=512"
            return self._model

    def rank(self, question: str, sources: list[dict], trace: dict | None = None) -> list[dict]:
        started = time.perf_counter()
        unique, seen, duplicates = [], set(), []
        for source in sources:
            key = (source["document_id"], source.get("page"), source.get("paragraph"), source["content"])
            if key in seen:
                duplicates.append(source["chunk_id"])
                continue
            seen.add(key)
            unique.append(source)
        sources = unique  # Only identical text at the same source location; never similarity dedup.
        with self._lock:
            model = self._load()
            tokenizer = model.tokenizer
            query_tokens = len(tokenizer(question, add_special_tokens=False)["input_ids"])
            pairs, windows, owners = [], [], []
            for owner, source in enumerate(sources):
                label = f"文件：{source['filename']}\n"
                budget = self.WINDOW - query_tokens - len(tokenizer(label, add_special_tokens=False)["input_ids"]) \
                    - tokenizer.num_special_tokens_to_add(pair=True) - 4
                if budget < 32:
                    raise RerankUnavailable("问题或文件名超过重排窗口预算，未截断问题或静默回退。")
                content = source["content"]
                offsets = tokenizer(content, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
                if not offsets:
                    continue
                step = max(1, budget - min(64, budget // 4))
                for start in range(0, len(offsets), step):
                    end = min(len(offsets), start + budget)
                    left, right = offsets[start][0], offsets[end - 1][1]
                    pairs.append((question, label + content[left:right]))
                    owners.append(owner)
                    windows.append({"chunk_id": source["chunk_id"], "start": left, "end": right})
                    if end == len(offsets):
                        break
            if len(pairs) > 4096:
                raise RerankUnavailable("重排窗口超过单次资源预算，未丢弃尾部证据；请缩小文档范围。")
            try:
                scores = model.predict(pairs, batch_size=self.BATCH, show_progress_bar=False) if pairs else []
                values = [float(score) for score in scores]
                if len(values) != len(pairs) or not all(math.isfinite(score) for score in values):
                    raise ValueError("invalid score shape/value")
            except Exception as exc:
                raise RerankUnavailable("本地重排执行失败，未静默退回旧排序。") from exc
            by_source = {}
            for owner, score, window in zip(owners, values, windows):
                by_source[owner] = max(by_source.get(owner, float("-inf")), score)
                window["score"] = score
            ordered = sorted(range(len(sources)), key=lambda i: (-by_source.get(i, float("-inf")), i))
            result = [dict(sources[i], rerank_score=by_source.get(i), rerank_rank=rank)
                      for rank, i in enumerate(ordered, 1)]
            if trace is not None:
                trace["rerank"] = {"model": settings.rerank_model, "device": "cpu", "batch_size": self.BATCH,
                                   "window_tokens": self.WINDOW, "windows": windows,
                                   "exact_duplicates_removed": duplicates,
                                   "order": [s["chunk_id"] for s in result],
                                   "latency_ms": (time.perf_counter() - started) * 1000}
            return result


reranker = Reranker()
