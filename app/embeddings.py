"""嵌入服务：真实后端（sentence-transformers / CPU）或 mock（仅离线接口测试）。

- 真实后端在后台线程加载，首次会自动下载 BAAI/bge-small-zh-v1.5 到 models 目录；
  下载/加载失败不阻塞服务启动（健康检查通过，只有用到嵌入的接口返回 503 说明）。
- mock 后端用文本哈希生成确定性伪向量（512 维、归一化），只用于跑通接口流程，
  检索结果无实际语义，禁止用于真实问答。
- 向量在入库与查询两侧都归一化，检索用 NumPy 点积（等价余弦相似度）。
"""
from __future__ import annotations

import hashlib
import threading

import numpy as np

from .config import settings

# BGE 官方建议：检索查询需要前缀指令（文档侧不加）
_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class EmbeddingUnavailable(Exception):
    pass


class EmbeddingService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model = None
        self._tokenizer = None
        self.state = "idle"     # idle | loading | ready | error
        self.message = ""
        self.dim = settings.embed_dim
        self._started = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if settings.embed_backend == "mock":
            self.state = "ready"
            self.message = "mock 嵌入后端：伪向量，仅用于离线接口测试"
            return
        self.state = "loading"
        self.message = "正在加载嵌入模型（首次启动会自动下载，请稍候）…"
        thread = threading.Thread(target=self._load_real, name="embed-loader", daemon=True)
        thread.start()

    def _load_real(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                settings.embed_model,
                device="cpu",
                cache_folder=str(settings.models_dir),
            )
            self._tokenizer = getattr(model, "tokenizer", None)
            get_dimension = getattr(model, "get_embedding_dimension", None)
            if get_dimension is None:
                get_dimension = model.get_sentence_embedding_dimension
            dim = int(get_dimension() or settings.embed_dim)
            with self._lock:
                self._model = model
            self.dim = dim
            self.state = "ready"
            self.message = f"嵌入模型就绪：{settings.embed_model}（{dim} 维）"
        except Exception as exc:  # noqa: BLE001 - 后台线程只记录状态
            self.state = "error"
            self.message = (
                f"嵌入模型加载失败：{exc}。若服务器无法访问 Hugging Face，"
                "请在有网机器运行 scripts/predownload_models.py 后，"
                "把 models 目录挂载到 /rag/models 再重启。"
            )

    # ---------- 对外接口 ----------

    def require_ready(self) -> None:
        if self.state != "ready":
            raise EmbeddingUnavailable(self.message or "嵌入模型尚未就绪")

    def tokenizer_or_none(self):
        """真实 tokenizer（用于精确切块）；mock/未就绪返回 None 走近似切块。"""
        return self._tokenizer if (self.state == "ready" and settings.embed_backend != "mock") else None

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_texts([text], query=True)[0]

    def embed_texts(self, texts: list[str], query: bool = False) -> np.ndarray:
        if not texts:
            raise EmbeddingUnavailable("没有文本可编码")
        self.require_ready()
        if settings.embed_backend == "mock":
            return self._mock_vectors(texts)
        model = self._model
        if model is None:
            raise EmbeddingUnavailable("嵌入模型未加载")
        if query:
            texts = [_QUERY_INSTRUCTION + t for t in texts]
        with self._lock:  # 串行化 encode：入库与查询共用同一模型实例
            vecs = model.encode(
                texts,
                batch_size=32,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        arr = np.asarray(vecs, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        return arr

    # ---------- mock ----------

    def _mock_vectors(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
            out[i] = np.random.default_rng(seed).standard_normal(self.dim)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (out / norms).astype(np.float32)


embedding_service = EmbeddingService()
