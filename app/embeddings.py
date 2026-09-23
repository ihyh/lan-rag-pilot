"""嵌入服务：真实后端（sentence-transformers，CPU 或 GPU）或 mock（仅离线接口测试）。

- 运行设备由 RAG_EMBED_DEVICE 决定（默认 cpu）。显式写成非 CPU 却不可用时会
  明确报错并让 /api/ready 返回 503，**不会静默退回 CPU**——否则"以为在用 GPU"
  这种问题只能靠猜。
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


def device_available(requested: str) -> tuple[bool, str]:
    """判断请求的嵌入设备在当前机器上是否可用。

    返回 (是否可用, 不可用原因)。不导入 torch 或导入失败时按不可用处理——
    因此本函数在无 torch 的测试环境（CI 的 smoke 依赖）里也能被完整覆盖。
    """
    device = (requested or "").strip().lower()
    if device == "cpu":
        return True, ""
    if not device:
        return False, "设备名为空"

    try:
        import torch
    except ImportError:
        return False, (
            f"未安装 PyTorch，无法使用 {device}；"
            "CPU 部署不受影响，或按 docs/MODELS.md 安装对应 CUDA 版 torch"
        )

    if device.startswith("cuda"):
        if not torch.cuda.is_available():
            return False, (
                f"未检测到可用的 CUDA 设备（torch.cuda.is_available() 为 False）；"
                f"若确认本机有 NVIDIA 显卡与驱动，请检查 torch 是否为 CUDA 版"
            )
        if ":" in device:
            try:
                index = int(device.split(":", 1)[1])
            except ValueError:
                return False, f"设备序号无法解析：{device}"
            count = torch.cuda.device_count()
            if index >= count:
                return False, f"设备 {device} 不存在：本机只检测到 {count} 个 CUDA 设备"
        return True, ""

    if device == "mps":
        backend = getattr(torch.backends, "mps", None)
        if backend is None or not backend.is_available():
            return False, "未检测到可用的 Apple MPS 设备"
        return True, ""

    return False, f"不支持的设备名：{device}"


class EmbeddingService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._model = None
        self._tokenizer = None
        self.state = "idle"     # idle | loading | ready | error
        self.message = ""
        self.dim = settings.embed_dim
        # 实际生效的设备；加载成功后写入模型真实落点，而不是回显配置值。
        self.device = settings.embed_device
        self._started = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if settings.embed_backend == "mock":
            self.state = "ready"
            self.device = "mock"
            self.message = "mock 嵌入后端：伪向量，仅用于离线接口测试"
            return
        self.state = "loading"
        self.message = (
            f"正在加载嵌入模型（设备 {self.device}，首次启动会自动下载，请稍候）…"
        )
        thread = threading.Thread(target=self._load_real, name="embed-loader", daemon=True)
        thread.start()

    def _load_real(self) -> None:
        requested = settings.embed_device
        ok, reason = device_available(requested)
        if not ok:
            # 显式请求了非 CPU 设备却不可用：如实报错，绝不静默退回 CPU。
            # 这属于配置问题而非启动阻塞——进程仍可启动，管理员能进后台改回来。
            self.state = "error"
            self.device = requested
            self.message = (
                f"嵌入设备 {requested} 不可用：{reason}。"
                "若暂不需要 GPU 编码，把 RAG_EMBED_DEVICE 改回 cpu 并重启即可恢复问答。"
            )
            return
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                settings.embed_model,
                device=requested,
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
            # 记录模型真实落到的设备，供 /api/health、/api/ready 与管理页核对。
            self.device = str(getattr(model, "device", requested))
            self.state = "ready"
            self.message = f"嵌入模型就绪：{settings.embed_model}（{dim} 维，{self.device}）"
        except Exception as exc:  # noqa: BLE001 - 后台线程只记录状态
            self.state = "error"
            self.device = requested
            self.message = (
                f"嵌入模型加载失败（设备 {requested}）：{exc}。若服务器无法访问 Hugging Face，"
                "请在联网准备机运行 python scripts/install_bge.py --project .，"
                "把生成的 models/bge-small-zh-v1.5 目录复制到目标机，"
                "将 RAG_EMBED_MODEL 指向该目录后再重启。"
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
                batch_size=settings.embed_batch_size,
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
