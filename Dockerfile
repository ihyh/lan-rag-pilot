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

# 先装 CPU 版 PyTorch（默认 PyPI 轮子带 CUDA，体积大数倍且 CPU 试点用不到）
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /rag/data/uploads /rag/models/hub

EXPOSE 8088

# 注意：单进程模型（进程内限流/并发闸门/内存向量索引依赖单 worker）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
