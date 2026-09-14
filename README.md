# 局域网知识库助手（LAN RAG Pilot）

[中文](README.md) | [English](README.en.md)

上传文档后，用户选择设备并提问；应用检索相关文档片段，调用本地 Ollama 生成回答并显示引用。运行组件是 RAG 应用（FastAPI、SQLite）、BGE 检索模型和 Ollama 生成模型。

| 使用方式 | 服务运行位置 | 用户如何访问 |
| --- | --- | --- |
| Windows 个人知识库 | 三个组件都在自己的 Windows 电脑 | 本机浏览器 `http://127.0.0.1:8088` |
| Linux 企业知识库 | 三个组件都在 Linux 服务器 | 员工连接公司 Wi-Fi，访问固定内网 HTTPS 地址（通常为 443 端口） |

两种方式都先完成“导入源码 → 创建虚拟环境 → 准备模型”，再按对应系统配置和启动。源码仓库不包含模型、密码或业务文档；允许联网的准备阶段可直接下载依赖和模型。

## Windows：个人知识库

准备 Windows、Python 3.12、PowerShell 和 [Ollama](https://ollama.com/download/windows)。在 PowerShell 执行，IDE 打开整个 `C:\rag` 文件夹：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3:1.7b
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
```

将 IDE 解释器设为 `C:\rag\.venv\Scripts\python.exe`。在 `C:\rag\.env` 新建以下配置，分别替换两个 `REPLACE`；可运行 `py -3.12 -c "import secrets; print(secrets.token_urlsafe(48))"` 两次生成独立的随机值。不要直接使用 `.env.example` 中的旧模型地址。

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

确认 Ollama 已启动，在 `C:\rag` 执行：

```powershell
powershell -NoProfile -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
```

打开 [http://127.0.0.1:8088](http://127.0.0.1:8088)。全新数据库用 `root` 和上述初始密码登录，上传测试文档，按设备提问并展开“查看引用来源”；停止时在启动窗口按 Ctrl+C。

## Linux：企业知识库

由 IT 在 Linux 服务器准备 Python 3.12、[Ollama](https://docs.ollama.com/linux)、内网域名及 HTTPS 证书。下面以 Ubuntu 和 `/opt/rag` 为例；先由 IT 创建可写的项目目录。以下下载命令仅在获准联网的安装阶段执行；如果服务器始终禁止公网，不要在服务器执行这些下载命令，应由 IT 按 [Ubuntu 指南](docs/UBUNTU.md)准备材料。

```bash
git clone https://github.com/ihyh/lan-rag-pilot.git /opt/rag
cd /opt/rag
python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
ollama pull qwen3:1.7b
./.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('models/bge-small-zh-v1.5')"
```

在 `/opt/rag/.env` 设置与 Windows 相同的本机 Ollama 地址和模型名，但把 `RAG_EMBED_MODEL` 改为 `/opt/rag/models/bge-small-zh-v1.5`，将 `RAG_PUBLIC_ORIGIN` 设为实际内网 HTTPS 地址，另设 `RAG_COOKIE_SECURE=true`。分别生成并填写新的 `RAG_SECRET_KEY` 与 `RAG_ROOT_PASSWORD`。先在服务器本机验证启动：

```bash
set -a
. ./.env
set +a
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
```

此命令仅供本机验收。员工访问前，IT 还须将应用设为受管服务，并通过 Nginx 等反向代理提供**内网 HTTPS**；只允许专用员工 Wi-Fi 网段访问入口，禁止公网到达，8088 和 Ollama 的 11434 端口不直接向员工设备开放。固定网址不能代替登录：root 为员工创建个人账号并授予角色，离职时停用。服务器部署、安全及备份步骤分别见 [Ubuntu 指南](docs/UBUNTU.md)、[安全要求](docs/SECURITY.md)和[运维指南](docs/OPERATIONS.md)。

| 管理员授予的角色 | 当前权限 |
| --- | --- |
| 普通用户 `user`（调试/开发/员工） | 按所选设备问答、看引用片段；不能打开完整原文或管理文档 |
| 文档管理员 `kb_admin` | 上述权限，加完整原文查看、上传、重新处理和删除文档 |
| 系统管理员 `root` | 上述全部，加账号、角色、系统设置和审计管理 |

设备选择依据文件名缩小检索范围，未匹配的资料需手动选择；它**不是访问授权**。当前所有获授权用户仍可问答共享文档库，未实现按部门/个人隔离。普通用户不能直接下载完整文件，但短文档可能完整落在一个引用片段中，多次问答也可能逐步获知内容。若业务要求不同人员不能获知同一资料，必须先实现文档级权限，不可直接作为满足该要求的正式系统使用。

模型下载后若要求运行时禁止公网，需由系统和网络出口策略实际阻断；`.env` 中的离线开关不等于防火墙。[第一次使用](docs/GETTING_STARTED.md) · [故障排查](docs/TROUBLESHOOTING.md)
