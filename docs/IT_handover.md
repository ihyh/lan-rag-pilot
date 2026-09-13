# 开发与配置索引

[返回首页](../README.md)

本页面向开发者和 IT。新手安装步骤拆分为 [Windows](WINDOWS.md)、[Ubuntu](UBUNTU.md)和[管理员离线包](OFFLINE_PACKAGE.md)；安全和运维分别见 [安全要求](SECURITY.md)、[运维](OPERATIONS.md)。旧版交接中实验地址、部门隔离和在线升级说明不再作为现行操作依据。

## 实现地图

| 文件 | 职责 |
|---|---|
| [config.py](../app/config.py)、[runtime.py](../app/runtime.py) | 环境默认值、数据库运行设置 |
| [main.py](../app/main.py)、[db.py](../app/db.py) | 初始化、HTTP 中间件、探针、SQLite |
| [security.py](../app/security.py)、[deps.py](../app/deps.py)、[auth.py](../app/routers/auth.py) | 哈希、会话、角色依赖、登录与密码 |
| [parsing.py](../app/parsing.py)、[chunking.py](../app/chunking.py)、[ingest.py](../app/ingest.py) | 文档解析、规则切片、入库 |
| [embeddings.py](../app/embeddings.py)、[index.py](../app/index.py) | CPU 嵌入、内存向量/关键词检索 |
| [llm.py](../app/llm.py)、[query.py](../app/routers/query.py) | 兼容模型请求、对话、流式结果与引用 |
| [admin.py](../app/routers/admin.py)、[前端脚本](../app/static/js/common.js) | 管理接口与共用表单 |

文档授权缺失必须视为企业隔离需求的阻塞项，不能因表中存在角色或历史部门字段就认为实现了 ACL。

## 配置规则与优先级

应用通过 os.environ 读取，**不会自动加载 .env**。Compose 的 env_file 和 Windows start_local.ps1 负责导入。独立 Python 脚本/直接 uvicorn 命令需要显式设置相同环境。

Windows 脚本不移除值两侧引号或行尾注释；Compose 则有自己的引号与变量插值语义。跨平台配置时必须复核，建议生成的密钥/初始口令采用足够长的 URL-safe 随机字符以减少解析歧义。不要打印完整生产环境或 Compose config。

数据库已有运行设置会覆盖 Top-K、每分钟请求数、并发数的初始环境值。修改 .env 后想改这三项，应在 root 管理页面核查；其余环境配置通常需进程重启/容器重建。

## 核心变量

| 变量 | 代码默认或作用 | 离线部署注意 |
|---|---|---|
| DEEPSEEK_BASE_URL | 缺失/空值回退公共 DeepSeek URL | 必须显式本机/内网兼容接口 /v1 |
| DEEPSEEK_MODEL | 缺失回退公共服务模型名 | 填已离线导入的精确本地标签 |
| DEEPSEEK_API_KEY | 默认空；模型调用要求非空 | 本文 Ollama 示例为 ollama，不是真正接口鉴权 |
| DEEPSEEK_TIMEOUT_S | 60 秒 | 教程 180，需与代理超时协调 |
| RAG_SECRET_KEY | 默认空，代码会用固定开发密钥 | 必须随机配置，缺失不自动阻止启动 |
| RAG_ROOT_PASSWORD | 空库没有初始密码时启动失败 | 只初始化首个 root，不重置已有用户 |
| RAG_COOKIE_SECURE | false | LAN HTTPS 必须 true |
| RAG_SESSION_TTL_HOURS | 168 小时 | 教程 8，由组织审定 |
| RAG_PUBLIC_ORIGIN | 空 | 对外地址信息，不是 ACL/防火墙 |
| RAG_HOST / RAG_PORT | 0.0.0.0 / 8088 | Windows 脚本用它们；直接 uvicorn 参数另行决定监听 |
| RAG_DATA_DIR | 项目 data | 部署时用明确持久路径 |
| RAG_DB_PATH / RAG_UPLOAD_DIR | data 下 rag.db / uploads | 数据与原文共同备份 |
| RAG_MODELS_DIR | 项目 models | 不等于 Ollama 模型目录 |
| RAG_EMBED_MODEL | BAAI/bge-small-zh-v1.5 | 教程用完整本地模型目录 |
| RAG_EMBED_BACKEND | st | mock 只用于测试，不可上线 |
| RAG_CHUNK_MAX_TOKENS / RAG_CHUNK_OVERLAP_TOKENS | 400 / 60 | 改后旧文档不自动重切 |
| RAG_TOP_K | 5 | 可被数据库运行设置覆盖 |
| RAG_MIN_RELEVANCE_SCORE | 0.25 | 需按模型/语料校准，不是答案可信度 |
| RAG_QUERIES_PER_MINUTE | 10 | 可被数据库运行设置覆盖 |
| RAG_MAX_CONCURRENT_LLM | 3 | 教程初始设 1；数据库设置优先 |
| RAG_LLM_MAX_TOKENS / RAG_LLM_TEMPERATURE | 1200 / 0.2 | 输出长度/随机性，不保证正确 |
| RAG_MAX_UPLOAD_MB | 25 | 与代理上限一起审查 |
| HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE | 依赖库识别 | 教程显式 1，不是通用外联阻断 |
| OLLAMA_NO_CLOUD / OLLAMA_MODELS | Ollama 进程读取 | 在 Ollama 环境设置，RAG 的 .env 不会自动改变外部 Ollama |

源码中的默认值不同于部署建议。`.env.example` 仍保留历史实验值，本次文档任务没有修改它；使用平台指南中的完整配置，替换占位值。

## 运行限制与 API

单 worker、单 RAG 副本。限流、并发闸门和向量索引依赖进程内状态，不能通过增加 uvicorn workers 安全扩容。多机/多进程需要额外架构改造。

本地启动后 /docs 提供 OpenAPI 文档，/api/health 用于存活，/api/ready 用于嵌入就绪；后者不验证 Ollama 全链路。生产应由 IT 控制管理/诊断入口范围，不把接口文档可见性当成授权。

改 env 不等于改数据库。升级可能涉及模式变化，先读 [备份回退](OPERATIONS.md)，不能把旧程序直接接到未知新库。

## 开发与验证

在独立源码副本、独立测试数据库和回环端口测试，**不能使用生产卷**。运行环境禁止公网；开发依赖同样由管理员 wheelhouse 导入。Node 用于前端静态回归，不是普通用户安装要求。

已装好离线依赖的 Windows 开发副本，在项目根目录可执行：

```powershell
node tests/form_modal_check.js
node tests/citations_check.js
node tests/device_scope_check.js
.\.venv\Scripts\python.exe tests/llm_request_check.py
.\.venv\Scripts\python.exe tests/retrieval_quality_check.py
.\.venv\Scripts\python.exe tests/conversation_scope_check.py
```

完整 mock smoke 会启动临时实例并写测试数据，使用新终端，确认两个端口空闲：

```powershell
powershell -NoProfile -File .\tests\smoke_runner.ps1 -Python .\.venv\Scripts\python.exe -AppPort 18092 -MockPort 18101
```

脚本中的弱测试凭据/密钥仅用于隔离临时库，不能复制到部署配置。mock 通过验证流程，不证明真实嵌入、Ollama 质量或服务器容量。真实验证见 [检索检查](RETRIEVAL_VALIDATION.md)、[人工评测](../eval/README.md)。

## 文档维护规则

新增功能要同时更新现行指南与限制，不复制历史机器 IP、密码或未经复验的“已上线”结论。静态审核、模拟测试、目标冷安装和真实业务验收分别记录。部署记录归档，不替代当前发布清单。
