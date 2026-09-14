# 局域网知识库助手（LAN RAG Pilot）

[中文](README.md) | [English](README.en.md)

将 PDF、Word、Excel 和文本导入知识库，用自然语言提问并查看答案的引用来源。运行时需要三部分：**RAG 应用**（FastAPI、SQLite、文档存储）、**本地 BGE 嵌入模型**（检索）和 **Ollama 生成模型**（回答）；用户通过浏览器访问。

## 先选择运行设备

| 场景 | 需要部署什么 | 从哪里开始 |
| --- | --- | --- |
| Windows 本机开发/单人使用 | 在 Windows 上运行 RAG、BGE 和 Ollama | 按下方步骤启动 |
| Ubuntu 内网服务器/多人使用 | 在 Ubuntu 上运行 RAG、BGE 和 Ollama；Windows 只需浏览器 | [Ubuntu 部署指南](docs/UBUNTU.md) |

**不必在 Windows 和 Ubuntu 各部署一套。**如果 Ubuntu 是 Windows 上的虚拟机，且希望 Ollama 使用 Windows 显卡，则属于“Ubuntu 跑 RAG、Windows 跑 Ollama”的跨机方案，需要单独配置虚拟机到宿主机的模型服务地址和防火墙；下方本机示例不适用。

## Windows：导入项目并启动

准备：Windows 11、Python 3.12、[Ollama](https://ollama.com/download/windows)；以下命令在 PowerShell 执行。联网只用于**准备源码、依赖和模型**。运行设备完全隔离时，先按[离线制包指南](docs/OFFLINE_PACKAGE.md)取得这些材料，不能在隔离设备执行下载命令。

1. 将源码导入设备。在可访问 GitHub 的准备机执行：

   ```powershell
   git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
   Set-Location C:\rag
   ```

   隔离设备则把准备好的源码包解压到 `C:\rag`。用 VS Code、PyCharm 等 IDE **打开 `C:\rag` 文件夹**，不是只打开某个 Python 文件。确认目录中有 `app`、`scripts` 和 `requirements.txt`。

2. 在 `C:\rag` 创建项目环境并准备模型。完成后 IDE 解释器选择 `C:\rag\.venv\Scripts\python.exe`。

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ollama pull qwen3:1.7b
   .\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
   ```

   前三条中的依赖安装、`ollama pull`，以及最后一条 BGE 下载都需要在获准联网的准备阶段执行。隔离设备改为导入匹配的 wheel 包和**完整**模型目录，具体命令见 [Windows 离线安装](docs/WINDOWS.md)。确认 `ollama list` 包含 `qwen3:1.7b`，且 `models\bge-small-zh-v1.5\modules.json` 存在。

3. 在项目根目录创建 `.env`（不要命名为 `.env.txt`），填写以下本机配置。将两个 `REPLACE` 值换成不同的强密码/随机密钥；可用 `py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"` 生成密钥。不要直接沿用 `.env.example` 的旧实验 IP。

   ```dotenv
   DEEPSEEK_API_KEY=ollama
   DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
   DEEPSEEK_MODEL=qwen3:1.7b
   RAG_EMBED_MODEL=models/bge-small-zh-v1.5
   RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
   RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
   RAG_HOST=127.0.0.1
   RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
   HF_HUB_OFFLINE=1
   TRANSFORMERS_OFFLINE=1
   ```

4. 确认 Ollama 已启动，在 `C:\rag` 运行：

   ```powershell
   powershell -NoProfile -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
   ```

   浏览器打开 [http://127.0.0.1:8088](http://127.0.0.1:8088)，新数据库以 `root` 和上述初始密码登录。上传一份测试文档，提问后展开“查看引用来源”，确认引用指向原文；停止服务时在启动窗口按 Ctrl+C。若模型或引用异常，见[故障排查](docs/TROUBLESHOOTING.md)。

## Ubuntu：导入项目并部署

在可联网的准备机克隆本仓库，或取得已核验的源码包，再将源码导入 Ubuntu 的新目录 `/opt/rag`。此外还需准备 Docker/Compose、RAG 与 Ollama 镜像、完整 BGE 和 Ollama 模型、`.env`、持久化数据卷及内网 HTTPS 入口。仓库自带 `docker-compose.yml` **只启动 RAG，不会安装或启动 Ollama**，也不会提供离线镜像和模型；不要把 `docker compose up` 当作完整部署。逐步操作见 [Ubuntu 离线部署](docs/UBUNTU.md)。

当前版本**没有部门或个人级文档访问隔离**，已登录用户可访问共享文档库；有此隔离要求时不能直接用于正式环境。严格断公网还需由系统/网络出口策略保证，不能仅靠 `.env` 开关。[安全要求](docs/SECURITY.md) · [模型选择](docs/MODELS.md) · [完整文档索引](docs/IT_handover.md)
