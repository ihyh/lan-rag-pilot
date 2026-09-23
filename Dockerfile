# 局域网 RAG 试点 —— 单体应用镜像（Python 3.12 + FastAPI + SQLite）
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/rag/models \
    TRANSFORMERS_CACHE=/rag/models/hub \
    HF_HUB_CACHE=/rag/models/hub \
    SENTENCE_TRANSFORMERS_HOME=/rag/models

WORKDIR /rag

# 先装 PyTorch。默认装 CPU 版：默认 PyPI 轮子带 CUDA，体积大数倍，而多数部署用不到。
# 需要 GPU 嵌入（RAG_EMBED_DEVICE=cuda）时用构建参数换成 CUDA 版，例如：
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124 .
# 默认值刻意保持不变，CPU 部署的镜像体积与行为不受影响。
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir torch --index-url "${TORCH_INDEX_URL}"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 解析旧版 Word 97-2003 .doc；放在 Python 依赖层之后，避免小改动重装大型 PyTorch。
RUN apt-get update \
    && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /rag/data/uploads /rag/models/hub

EXPOSE 8088

# 注意：单进程模型（进程内限流/并发闸门/内存向量索引依赖单 worker）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
