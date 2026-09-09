# 局域网知识库助手（LAN RAG）

把设备手册、操作说明等资料上传到公司内网后，员工可以像聊天一样提问，并查看答案引用了哪份文档。

它不是普通聊天机器人：系统会先从已上传的资料中查找相关内容，再让内网中的 AI 模型整理答案。找不到可靠资料时，它会明确说无法回答。

> 当前版本为内部试点版 v0.1.0，适合小团队在隔离局域网中使用。系统不会自动改用公网模型。

## 它解决什么问题

以前查设备资料，通常需要打开多份 PDF 或 Word，再逐页搜索关键词。本项目把这个过程变成：

```text
上传文档 → 系统解析和建立索引 → 用户提问 → 查找相关原文 → AI 生成答案 → 展示引用来源
```

例如：

- “PLUSPRO 点动速度怎么调整？”
- “PLM 出现某个报警后应该检查什么？”
- “PLUS500 的某个参数范围是多少？”

回答下方会列出命中的文件、页码或段落，方便继续打开原文确认。

## 目前能做什么

| 功能 | 小白理解 |
|---|---|
| 上传资料 | 支持 PDF、DOC、DOCX、XLSX、TXT、MD |
| 选择设备 | 提问前可以选择 PLM、PLUSPRO、PLUS500，也可以手动选择文档 |
| 连续追问 | 同一个对话中可以继续问，不必每次重复背景 |
| 引用原文 | 每次回答都会显示本轮实际使用的资料来源 |
| 管理文档 | 管理员可以上传、删除或重新处理文档 |
| 管理账号 | root 可以创建、停用账号和重置密码 |
| 收集反馈 | 用户可以标记回答“有帮助”或“没帮助” |
| 内网运行 | 文档、数据库和模型都可以放在局域网内 |

## 目前不能做什么

- 不支持扫描图片型 PDF 的文字识别，也就是暂时没有 OCR。
- 不支持旧版 XLS、PPT 和 PPTX。
- 不会自动联网搜索，也不会自动回退到 DeepSeek 等公网模型。
- 所有登录用户共享同一套文档，不提供部门之间的资料隔离。
- AI 回答可能有误，重要参数和操作步骤必须核对引用原文。
- 当前是单机试点架构，不适合直接作为大型互联网服务。

## 普通用户怎么使用

1. 打开管理员提供的局域网网址并登录。
2. 点击“新建对话”。
3. 选择要询问的设备或具体文档。
4. 输入一个尽量明确的问题，然后发送。
5. 查看回答，并展开“引用来源”核对原文。
6. 同一主题可以继续追问；要切换设备时，请新建对话。

提问越具体，通常越容易得到准确答案。例如：

```text
不够具体：速度怎么调？
更清楚：PLUSPRO 点动速度在哪个参数页面调整？可调范围是多少？
```

## 管理员怎么使用

管理员登录后进入“管理”页面：

1. 上传文档。
2. 等待文档状态变成“可用”。
3. 回到问答页面进行测试。
4. 如果文档解析失败，查看失败原因后处理原文件。

“重新处理”不会重新上传文件。它会再次解析已经保存的原文件、重新切片并建立检索索引，适用于模型恢复、上次处理失败或解析逻辑升级后的情况。

## 系统是怎么工作的

```text
浏览器
  ↓
FastAPI 应用：登录、权限、文档管理、问答
  ↓
本地 BGE：从文档中找出与问题最相关的片段
  ↓
内网 Ollama：只根据这些片段组织答案
  ↓
SQLite：保存账号、文档索引、对话、引用和反馈
```

当前默认组件：

- Web 服务：FastAPI
- 数据库：SQLite
- 检索模型：`BAAI/bge-small-zh-v1.5`
- 生成模型：Ollama 中的 `qwen3:1.7b`
- 部署方式：Docker Compose
- 默认服务端口：`8088`

完整原文件不会作为整体发送给生成模型。模型只接收当前问题、检索到的少量片段和有限的对话历史。

## 快速部署

下面是最短部署流程。第一次部署建议由了解 Docker 和局域网配置的人员操作。

### 1. 准备环境

需要：

- 一台运行 RAG 服务的电脑或服务器，已安装 Git、Docker 和 Docker Compose。
- 一台能运行 Ollama 的电脑；它可以和 RAG 服务在同一台机器上。
- RAG 服务能够通过局域网访问 Ollama 的 `11434` 端口。
- 第一次准备 BGE 模型时可以访问 Hugging Face，或者已经有离线模型目录。

先在模型电脑上安装并启动 Ollama，再准备模型：

```bash
ollama pull qwen3:1.7b
```

如果 Ollama 和 RAG 不在同一台电脑，还需要让 Ollama 监听局域网地址并配置防火墙。不要把 Ollama 端口暴露到公网。

### 2. 下载项目

```bash
git clone https://github.com/ihyh/lan-rag-pilot.git
cd lan-rag-pilot
```

### 3. 创建配置文件

Linux：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

然后用文本编辑器打开 `.env`，至少确认下面这些设置：

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://192.168.1.10:11434/v1  # 示例：改成 Ollama 电脑的实际地址
DEEPSEEK_MODEL=qwen3:1.7b
RAG_ROOT_PASSWORD=
RAG_SECRET_KEY=
RAG_PUBLIC_ORIGIN=http://192.168.1.20:8088     # 示例：改成 RAG 服务器的实际地址
```

其中 `DEEPSEEK_*` 是为了兼容 OpenAI 风格接口保留的历史变量名。这里实际连接的是内网 Ollama，不是公网 DeepSeek。

`RAG_ROOT_PASSWORD` 和 `RAG_SECRET_KEY` 不能留空：前者填写首次登录使用的强口令，后者填写下面命令生成的随机字符串。

可以用下面的命令生成 `RAG_SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`.env` 中可能包含口令和密钥，已经被 Git 忽略，不要上传或发给他人。

### 4. 构建并启动

```bash
docker compose build
docker compose run --rm -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 rag python scripts/predownload_models.py
docker compose up -d --no-build
docker compose ps
```

当 `rag-pilot` 显示为 `healthy` 后，在浏览器访问：

```text
http://RAG服务器地址:8088
```

检查接口：

```bash
curl http://127.0.0.1:8088/api/health
curl http://127.0.0.1:8088/api/ready
```

- `/api/health` 表示程序已经启动。
- `/api/ready` 表示检索模型已经加载，可以上传文档和提问。

### 5. 第一次登录后

1. 使用 `.env` 中设置的 root 口令登录。
2. 立即确认或修改 root 口令。
3. 创建普通用户或文档管理员账号。
4. 上传一份不含机密的测试文档。
5. 提问并检查引用是否来自正确文档。

## 电脑重启后怎么恢复

容器设置了 `restart: unless-stopped`，Docker 正常启动后，RAG 容器通常会自动恢复。仍建议执行：

```bash
cd lan-rag-pilot
docker compose up -d
docker compose ps
```

同时确认 Ollama 已启动：

```bash
ollama list
```

如果网页能打开但提问超时，通常先检查 Ollama 是否运行、模型是否存在，以及 RAG 服务器能否访问 Ollama 地址。

## 数据保存在哪里

Docker 部署默认使用两个数据卷：

| 数据卷 | 保存内容 |
|---|---|
| `rag-pilot_rag_data` | 数据库和上传的原文件 |
| `rag-pilot_rag_models` | BGE 检索模型 |

停止或更新容器不会自动删除数据卷。不要随意执行 `docker compose down -v`，其中的 `-v` 会删除数据卷。

GitHub 仓库不包含生产数据库、上传文档、模型、备份、`.env` 或本地工作记录。

## 三种账号有什么区别

| 账号 | 权限 |
|---|---|
| `user` | 提问、查看和删除自己的对话、查看引用、提交反馈 |
| `kb_admin` | 包含 user 权限，并可上传、删除和重新处理文档 |
| `root` | 包含全部权限，并可管理用户、全体对话、反馈、审计和参数 |

个人对话只对本人和 root 可见，但所有启用账号都可以检索共享文档库。

## 常见问题

### 为什么回答“根据知识库现有内容无法回答”？

常见原因：选错设备或文档、文档还没有处理完成、问题太模糊，或者资料中确实没有答案。先确认文档状态为“可用”，再选择正确设备并把型号、参数名和现象写清楚。

### 为什么回答中混入了另一台设备？

新建对话时选择正确设备或具体文档。文档范围会绑定到整个对话；切换设备时应新建对话，不要在原对话中继续问。

### 为什么回答很慢？

主要时间通常花在 Ollama 生成答案。CPU 运行模型会比较慢，使用兼容 GPU 可以明显提速。文档检索、问题长度、模型大小和同时提问人数也会影响耗时。

### 为什么上传不了 DOC？

系统支持真正的 Word 97–2003 `.doc` 文件。如果只是把其他文件改成 `.doc` 后缀，或者文件已经损坏，系统会拒绝。建议先用 Word 打开确认，再重新保存或转换成 DOCX。

### 为什么网页地址重启后变了？

服务器使用了 DHCP 动态地址。正式使用前应让 IT 设置固定 IP 或内部域名，然后同步更新 `.env` 中的 `RAG_PUBLIC_ORIGIN`。

### 网页能打开，但提问显示超时怎么办？

依次检查：

1. `ollama list` 能否看到配置的模型。
2. Ollama 服务是否正在运行。
3. `.env` 中的模型名称和地址是否正确。
4. RAG 服务器能否访问 Ollama 的 `11434` 端口。
5. `docker compose logs -f rag` 中是否有模型连接错误。

## 当前项目状态

- 已实现多轮对话、设备/文档范围、混合检索、引用、反馈和三级权限。
- 已支持 PDF、DOC、DOCX、XLSX、TXT、MD。
- 最近一次完整隔离 smoke 测试为 **145/145 通过**，重启持久化通过。
- 当前 Ubuntu 试点环境使用 BGE 检索和内网 Ollama `qwen3:1.7b`。
- 正式推广前仍需完成固定地址、HTTPS、独立备份和真实问题质量评测。

自动化测试只能证明功能流程正常，不能证明每个真实问题都能得到高质量答案。

## 给开发和运维人员

常用命令：

```bash
docker compose logs -f rag
docker compose restart rag
docker compose config --quiet
```

Windows 完整冒烟测试：

```powershell
.\tests\smoke_runner.ps1 -Python .\.venv\Scripts\python.exe -AppPort 18092 -MockPort 18101
```

更多资料：

- [项目路线图](ROADMAP.md)
- [Ubuntu 当前部署记录](DEPLOYMENT_HANDOFF.md)
- [IT 部署与交付清单](docs/IT_handover.md)
- [安全与上线检查](docs/SECURITY_AUDIT.md)
- [企业微信 / 飞书工作台接入](docs/WORKBENCH_INTEGRATION.md)
- [真实问题质量评测](eval/README.md)
- 启动后访问 `/docs` 查看完整 API 文档

## 安全提醒

当前版本仍属于隔离局域网试点。存放敏感资料前，至少完成：

- 使用 HTTPS，并设置 `RAG_COOKIE_SECURE=true`。
- 使用强 root 口令和随机 `RAG_SECRET_KEY`。
- 防火墙只允许指定内网访问。
- 定期备份数据卷，并验证备份可以恢复。
- 不把 `.env`、数据库、上传文档或模型服务端口暴露到公网。

详细要求请阅读 [安全与上线检查](docs/SECURITY_AUDIT.md)。
