# 模型与配置

## 生成模型

新安装默认 [Ollama qwen3:4b-instruct](https://ollama.com/library/qwen3:4b-instruct)。当前官方量化文件约 2.5 GB，实际运行还需要 KV 缓存和额外内存，不能把下载大小当作显存需求。

```powershell
ollama list
ollama pull qwen3:4b-instruct
ollama ps
```

`.env` 使用本机 OpenAI 兼容接口：

```dotenv
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_API_KEY=ollama
DEEPSEEK_MODEL=qwen3:4b-instruct
RAG_LLM_TRUST_ENV_PROXY=0
```

变量沿用兼容接口的历史命名，不表示必须连接某个公网服务。启动校验拒绝未受信任的公网模型地址。安装脚本保留已有配置，不擅自切换正在使用的模型；手动修改后重启并做隔离对照测试。模型更大不等于对当前文档一定更准确。

Ollama 的驻留时长等服务端变量需设置在真正启动 Ollama 的环境中；仅修改应用 `.env` 不保证已经运行的 Ollama 继承设置。用 `ollama ps` 检查实际运行情况。

## 嵌入模型

BGE-small-zh-v1.5，512 维；默认位置 `models/bge-small-zh-v1.5`，通过 `scripts/install_bge.py` 安装并校验。应用使用本地模型文件，不随提问上传私人文档到模型下载站。

默认 `RAG_EMBED_DEVICE=cpu`。自动安装使用 CPU 版 PyTorch；若需要 CUDA，先准备兼容驱动和 CUDA 版 PyTorch，再设置 `cuda` 或 `cuda:0`。配置不可用设备会显式报错，不静默退回 CPU。检查就绪探针和管理页实际设备；生成模型和嵌入模型会共享有限的显存资源。

改变嵌入模型或维数后，需要在备份基础上重建索引并验证；不要混用旧向量。仅改变生成模型通常无需重建，但仍需测试答案和引用。所有对照在独立目录、非生产端口进行，保留原答案和失败证据。
