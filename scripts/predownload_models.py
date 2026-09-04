"""离线预下载嵌入模型（供无法访问 Hugging Face 的服务器使用）。

用法：
  1) 在能访问 Hugging Face 的机器上（本机或临时容器）：
       docker compose build
       docker compose run --rm rag python scripts/predownload_models.py
     （或本机先 pip install -r requirements.txt 后直接 python scripts/predownload_models.py）
  2) 把下载好的 models 目录整体拷贝到目标服务器，并在 compose 挂载到 /rag/models：
     volumes: - <本地路径>/models:/rag/models
  3) 目标服务器离线启动即可（模型命中本地缓存，无需联网）。

也支持 HF_ENDPOINT=https://hf-mirror.com 走国内镜像加速。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


def main() -> None:
    if settings.embed_backend == "mock":
        print("RAG_EMBED_BACKEND=mock：伪向量后端，无需下载模型。")
        return
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    print(f"加载/下载嵌入模型: {settings.embed_model}")
    print(f"缓存目录: {settings.models_dir}")
    import sentence_transformers

    model = sentence_transformers.SentenceTransformer(
        settings.embed_model,
        device="cpu",
        cache_folder=str(settings.models_dir),
    )
    get_dimension = getattr(model, "get_embedding_dimension", None)
    if get_dimension is None:
        get_dimension = model.get_sentence_embedding_dimension
    dim = get_dimension()
    print(f"模型就绪，向量维度: {dim}")
    print("完成。离线部署：将 models 目录拷贝到目标机并挂载到容器 /rag/models。")


if __name__ == "__main__":
    main()
