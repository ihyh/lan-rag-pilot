# Windows 个人离线安装

[返回首页](../README.md) · [准备离线包](OFFLINE_PACKAGE.md)

适用 Windows 11 x64、一人同机使用；浏览器、RAG 与 Ollama 都在本机，不开放给其他电脑。团队共享见 [Ubuntu](UBUNTU.md)及[安全要求](SECURITY.md)。

`C:\rag` 是**新安装目录示例**，不是覆盖原项目的指令。已有安装先看 [备份迁移](OPERATIONS.md)。命令在 PowerShell 执行，失败先停下排查。

## 1. 核验并安装基础软件

管理员交付：项目源码、Python 3.12 x64 完整安装程序、Ollama 完整离线安装材料、锁定版本的 wheelhouse、完整嵌入和生成模型、版本清单及可信校验值。显卡驱动、系统运行库应在包内或由 IT 预装。

断开公网路径，按离线包说明校验。使用包内程序安装 Python 和 Ollama，拒绝在线补充下载；缺材料请管理员重新制包。

源码解压到空的 `C:\rag`，其他材料放 `C:\rag-kit`。路径不同请统一调整示例：

```powershell
py -3.12 --version
Get-Item C:\rag\app\main.py
Get-Item C:\rag-kit\windows\requirements.lock.txt
```

成功标准：Python 是验收过的 3.12.x x64，文件存在。没有 Python Launcher 时，以管理员提供的 python.exe 完整路径代替 `py -3.12`。

## 2. 安装依赖

```powershell
Set-Location C:\rag
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links C:\rag-kit\windows\wheelhouse -r C:\rag-kit\windows\requirements.lock.txt
.\.venv\Scripts\python.exe -m pip check
```

不需激活虚拟环境。成功标准：依赖无冲突。找不到 wheel 表示包不完整或平台不匹配，不能在线弥补。不要跨机器复制 `.venv`。

## 3. 导入本地模型

将完整嵌入模型放到 `C:\rag\models\bge-small-zh-v1.5`，包括权重、tokenizer、配置，不能只有权重文件。

退出 Ollama 托盘程序。把交付的 Ollama 模型复制到**新空目录** `C:\rag\ollama-models`；其下直接包含 `blobs`、`manifests`，不要多套一层 models，不覆盖旧模型库。

设置当前用户环境后，必须以同一用户重新启动 Ollama 才生效：

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', 'C:\rag\ollama-models', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD', '1', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_CONTEXT_LENGTH', '8192', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_NUM_PARALLEL', '1', 'User')
```

重新登录 Windows，从开始菜单启动 Ollama，再打开新 PowerShell：

```powershell
ollama list
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

成功标准：包含清单指定的模型标签/ID。后续示例用 `qwen3:4b`，其他选择必须同步修改配置。断网机不执行 `ollama pull`。

参考 [Ollama FAQ](https://docs.ollama.com/faq)与 [Windows 安装说明](https://docs.ollama.com/windows)。关闭云功能仍不能代替系统出口策略。

## 4. 创建配置

用编辑器新建 `C:\rag\.env`，不能是 `.env.txt`，也不要沿用仓库示例的旧实验 IP：

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen3:4b
DEEPSEEK_TIMEOUT_S=180
RAG_SECRET_KEY=REPLACE_WITH_RANDOM_SECRET
RAG_ROOT_PASSWORD=REPLACE_WITH_STRONG_INITIAL_PASSWORD
RAG_COOKIE_SECURE=false
RAG_SESSION_TTL_HOURS=8
RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
RAG_HOST=127.0.0.1
RAG_PORT=8088
RAG_DATA_DIR=C:/rag/data
RAG_DB_PATH=C:/rag/data/rag.db
RAG_UPLOAD_DIR=C:/rag/data/uploads
RAG_MODELS_DIR=C:/rag/models
RAG_EMBED_MODEL=C:/rag/models/bge-small-zh-v1.5
RAG_EMBED_BACKEND=st
RAG_MAX_CONCURRENT_LLM=1
NO_PROXY=127.0.0.1,localhost
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

下面只生成随机值，将输出填入 SECRET；不要传播或提交。另设唯一强初始密码，**替换全部 REPLACE 占位值后**再启动：

```powershell
C:\rag\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

限制 `.env` 和 data 仅本人及必要管理员可读。启动脚本按原样读等号后的值，**不要加外层引号或行尾注释**；注释放独立行。仅个人 HTTP 回环用 Secure=false，LAN 入口必须 HTTPS/true。

## 5. 启动验证

```powershell
Set-Location C:\rag
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_local.ps1 -Python C:\rag\.venv\Scripts\python.exe
```

保持窗口打开。这里的 Bypass 只作用于本次子进程，不修改全局执行策略；组织策略若仍禁止脚本，请 IT 审核签名/允许方式。脚本默认 Python 路径是历史测试路径，正常安装必须指定 `-Python`。

另开 PowerShell：

```powershell
Invoke-RestMethod http://127.0.0.1:8088/api/health
Invoke-RestMethod http://127.0.0.1:8088/api/ready
```

加载期间 ready 可暂时 503；变为 200/ready 后打开 [本机页面](http://127.0.0.1:8088)，用 root/初始密码登录，完成 [演示问答与引用](GETTING_STARTED.md)。ready 不检查完整生成链路，必须实际提问。

## 6. 停止与重启

在 RAG 窗口 Ctrl+C，等退出后关闭；Ollama 单独从托盘退出。电脑重启后先启动 Ollama，再执行第 5 步。本教程不自动创建 Windows 后台服务。

Windows 原生安装不自动提供 antiword。需要旧 DOC 时请 IT 提供受信任版本并验证；否则在本地 Word 转 DOCX，不能把内部文件上传到在线转换网站。

下一步：[备份与升级](OPERATIONS.md)。不要仅修改监听为 0.0.0.0 就让多人使用，必须重新审查授权、TLS 与网络边界。
