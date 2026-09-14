# 局域网知识库助手（LAN RAG Pilot）

[中文](README.md) | [English](README.en.md)

将 PDF、Word、Excel 和文本资料导入本地知识库后，用户可以用自然语言提问，并打开回答所引用的原文位置。服务由 FastAPI、SQLite、本地 BGE 嵌入模型和 Ollama 生成模型组成；浏览器负责交互，资料与模型保存在运行电脑或内网服务器。

`文档上传 → 解析与切片 → BGE 检索相关片段 → Ollama 生成回答 → 显示引用`

下面先让开发者在 Windows 上完成一次真实问答。服务器离线部署和长期运维见文末专题文档。

## 开发环境要求

- Windows 11 x64、PowerShell、Python 3.12 x64、Ollama，以及足以运行本地生成模型的内存/磁盘。入门示例使用 `qwen3:1.7b`；按硬件调整见[模型选择](docs/MODELS.md)。
- 选用一个**新的** `C:\rag` 作为项目目录，`C:\rag-local` 存放模型与数据。以下命令均在 PowerShell 运行；路径不同时请保持所有步骤一致。
- 联网开发电脑只能在准备阶段下载源码、依赖与模型，正式运行前断开公网；完全隔离的开发电脑须先从管理员取得源码、Python/Ollama 安装包、依赖 wheelhouse、完整 BGE 目录和 Ollama 模型目录，参见[离线包制作](docs/OFFLINE_PACKAGE.md)。
- Ollama 必须已安装。首次启动还需要在 `.env` 中设置随机会话密钥和 root 初始密码。仓库不包含这些秘密、业务数据或模型文件。

## Windows 快速开始

### 1. 获取源码并导入 IDE

在允许访问 GitHub 的**准备阶段**，可直接克隆：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
```

完全隔离的电脑从已核验的离线包导入，假设包中的源码根目录为 `C:\rag-kit\project`：

```powershell
New-Item -ItemType Directory C:\rag -ErrorAction Stop
Copy-Item C:\rag-kit\project\* C:\rag -Recurse -ErrorAction Stop
Set-Location C:\rag
Test-Path .\app\main.py
```

两种方式只选一种。最后一条命令应显示 `True`。在任意 IDE 中打开 `C:\rag` 文件夹；创建虚拟环境后，将项目解释器设为 `C:\rag\.venv\Scripts\python.exe`。不要把其他机器的 `.venv` 复制过来。

### 2. 安装 Python 依赖

以下命令都在 `C:\rag` 中执行：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
```

若这台开发电脑仍处于获准的**联网准备阶段**：

```powershell
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

若电脑始终隔离，使用管理员为同一 Windows/Python 架构准备的包：

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links C:\rag-kit\windows\wheelhouse -r C:\rag-kit\windows\requirements.lock.txt
```

最后执行 `.\.venv\Scripts\python.exe -m pip check`，预期无依赖冲突。缺少 wheel 时回到准备机补包，不在隔离电脑联网安装。

### 3. 准备两个本地模型

先创建模型目录，并为**当前 Windows 用户**设置 Ollama 的目录和本机监听地址。退出已运行的 Ollama 托盘程序，然后执行：

```powershell
New-Item -ItemType Directory C:\rag-local\models,C:\rag-local\ollama-models -Force | Out-Null
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', 'C:\rag-local\ollama-models', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD', '1', 'User')
```

重新登录 Windows，从开始菜单启动 Ollama，再打开新的 PowerShell。在获准联网的**准备阶段**，取得示例模型：

```powershell
ollama pull qwen3:1.7b
Set-Location C:\rag
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu').save('C:/rag-local/models/bge-small-zh-v1.5')"
```

完全隔离的电脑不运行以上下载命令，而是导入管理员已核验的完整模型包：

```powershell
Copy-Item C:\rag-kit\models\bge-small-zh-v1.5 C:\rag-local\models -Recurse -ErrorAction Stop
Copy-Item C:\rag-kit\ollama-models\* C:\rag-local\ollama-models -Recurse -ErrorAction Stop
```

离线包中的 Ollama 目录应直接包含 `blobs` 和 `manifests`。验证：

```powershell
ollama list
Test-Path C:\rag-local\models\bge-small-zh-v1.5\modules.json
```

应看到 `qwen3:1.7b` 和 `True`。模型目录不可只放权重文件；缺文件应重新制作离线包。完成联网准备后，关闭公网访问再启动项目。

### 4. 配置本地服务

在 `C:\rag` 新建 `.env`（不是 `.env.txt`），填入以下内容并替换两个 `REPLACE` 值。不要直接沿用 `.env.example` 中的历史实验地址：

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen3:1.7b
DEEPSEEK_TIMEOUT_S=180
RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
RAG_COOKIE_SECURE=false
RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
RAG_HOST=127.0.0.1
RAG_PORT=8088
RAG_DATA_DIR=C:/rag-local/data
RAG_DB_PATH=C:/rag-local/data/rag.db
RAG_UPLOAD_DIR=C:/rag-local/data/uploads
RAG_MODELS_DIR=C:/rag-local/models
RAG_EMBED_MODEL=C:/rag-local/models/bge-small-zh-v1.5
RAG_EMBED_BACKEND=st
RAG_MAX_CONCURRENT_LLM=1
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

运行 `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"` 生成随机密钥，再为 root 设置独立的强初始密码。两者都不可提交或发送给他人。Windows 启动脚本按原样读取等号后的值，不能给值加外层引号或行尾注释。`DEEPSEEK_*` 是兼容接口沿用的变量名，此配置只连接本机 Ollama。

### 5. 启动并确认就绪

在 `C:\rag` 运行：

```powershell
powershell -NoProfile -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
```

保持此窗口打开。若执行策略阻止脚本，请按组织政策处理，不要全局关闭策略。另开 PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8088/api/health
Invoke-RestMethod http://127.0.0.1:8088/api/ready
```

加载 BGE 时 `ready` 可能暂时返回 503；等待变成 200 后打开 [http://127.0.0.1:8088](http://127.0.0.1:8088)。首次登录用户名为 `root`，密码是刚写入的初始密码。已有数据库不会因修改 `RAG_ROOT_PASSWORD` 而重置密码。停止服务时在启动窗口按 Ctrl+C；Ollama 单独退出。

## 完成一次真实问答并核对引用

登录后进入“管理”，上传以下 UTF-8 文本作为 `入门演示.txt`（仅为虚构测试资料）：

```text
演示设备：DEMO-01。
DEMO-01 的每日巡检时间是上午 09:00。
巡检记录由值班员填写。
```

等文档状态为“可用”，新建对话并限定这份文档，提问“DEMO-01 每日巡检时间是几点？”。等待流式输出结束，再展开“查看引用来源”：答案应为 09:00，来源应指向 `入门演示.txt` 中支持该结论的原文。回答、文档状态或引用任何一项不对，都不能算安装成功；按[故障排查](docs/TROUBLESHOOTING.md)检查。 `/api/ready` 只检查嵌入就绪，不验证完整生成链路。

`tests/smoke_runner.ps1` 使用模拟嵌入和模拟生成服务，只用于接口回归；它不能替代上述真实模型与引用验收。

## 模型与运行方式

| 环节 | 当前实现 | 更换时注意 |
|---|---|---|
| 分词与切片 | BGE tokenizer + 项目规则，默认上限 400 token、重叠 60 | 不是独立切片模型；改参数后旧文档需重处理 |
| 检索 | BGE-small 中文 v1.5，512 维，在 CPU 上编码 | 更换权重后需重新生成文档向量 |
| 生成 | 本地 Ollama；本例为 `qwen3:1.7b` | 更换生成模型通常无需重建文档，但应比较回答和资源占用 |

较大模型可能改善召回或表达，也会增加内存、显存和延迟；下载大小不等于运行内存。硬件建议与切换方法见[模型指南](docs/MODELS.md)。

## 详细文档

- [Windows 个人离线安装](docs/WINDOWS.md)、[Ubuntu 内网服务器部署](docs/UBUNTU.md)、[管理员离线包制作](docs/OFFLINE_PACKAGE.md)
- [第一次使用](docs/GETTING_STARTED.md)、[备份迁移与升级](docs/OPERATIONS.md)、[故障排查](docs/TROUBLESHOOTING.md)
- [开发与配置索引](docs/IT_handover.md)、[检索验证](docs/RETRIEVAL_VALIDATION.md)、[业务质量评测](eval/README.md)
- [安全上线要求](docs/SECURITY.md)、[路线图](ROADMAP.md)、[安全问题报告](SECURITY.md)

## 安全与部署限制

本机 HTTP 示例仅绑定 `127.0.0.1`；供其他电脑使用时需要经批准的内网 HTTPS、受控账号、端口及出口策略。当前版本**没有部门/个人级文档访问隔离**：已登录用户可读取共享文档库。若企业要求不同部门或人员不能互读资料，当前版本不得作为满足该要求的正式系统使用。

`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`、`OLLAMA_NO_CLOUD` 不能代替防火墙；程序若缺少本地模型地址，还存在公共接口默认值。因此严格离线必须由网络策略和显式本地配置共同保证。SQLite、上传原文及备份需要访问控制和存储保护，重要答案须核对原始资料。详见[安全要求](docs/SECURITY.md)。
