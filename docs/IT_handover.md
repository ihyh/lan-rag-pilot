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
| DEEPSEEK_BASE_URL | 缺失/空值回退公共 DeepSeek URL | 必须显式本机/内网兼容接口 /v1；**主机非内网时拒绝启动** |
| DEEPSEEK_MODEL | 缺失回退公共服务模型名 | 填已离线导入的精确本地标签 |
| DEEPSEEK_API_KEY | 默认空；模型调用要求非空 | 本文 Ollama 示例为 ollama，不是真正接口鉴权 |
| DEEPSEEK_TIMEOUT_S | 60 秒 | 教程 180，需与代理超时协调 |
| RAG_LLM_TRUST_ENV_PROXY | 0（不使用代理） | **不要改成 1**，除非确实要经代理访问公网 API；详见下方“代理接管”一节 |
| RAG_SECRET_KEY | 默认空，**缺失时拒绝启动**（不再回退开发密钥） | 必须随机配置；错误信息内含生成命令 |
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
| RAG_TOP_K | 3 | 可被数据库运行设置覆盖 |
| RAG_READY_PROBE_LLM | 1 | 就绪是否校验生成模型；关闭后探针不再覆盖问答链路 |
| RAG_READY_PROBE_TTL_S | 30 秒 | 探测结果缓存；0 表示每次健康检查都真探测 |
| RAG_READY_PROBE_TIMEOUT_S | 3 秒 | 须明显小于编排层健康检查超时（compose 为内层 8 秒、外层 10 秒） |
| RAG_PARSE_ISOLATION | 1（启用） | 解析放到子进程执行；单 worker 下这是防"一份坏文件拖垮全站"的关键一层，见下方「解析隔离」一节 |
| RAG_PARSE_TIMEOUT_S | 300 秒 | 单份文件解析硬超时；超时后杀掉整棵进程树，不留孤儿 |
| RAG_PARSE_MEMORY_MB | 2048 | 解析子进程内存上限，0 为不限制；**仅 POSIX 生效**（RLIMIT_AS），Windows 只有超时与崩溃隔离 |
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

本地启动后 /docs 提供 OpenAPI 文档，/api/health 用于存活，/api/ready 用于就绪。两者的分工必须分清：

- `/api/health`：只反映进程活着，**从不主动联系 Ollama**，永远 200。它顺带返回上次就绪探测的缓存结果（没有缓存时为 `null`）。存活探针不去碰外部依赖，是为了避免 Ollama 抖动导致编排层把本来能提供检索服务的容器反复重启。
- `/api/ready`：同时校验嵌入模型与生成模型两条链路。嵌入未就绪先返回 `503 reason=embed_not_ready`（此时不会去探测模型服务）；嵌入就绪但模型不可用返回 `503 reason=llm_not_ready`，响应体 `checks.embed` 与 `checks.llm` 分别给出两条链路的结论与可操作说明。`model_ready` 字段为兼容旧脚本保留，只表示嵌入。
- 探测结果带 TTL 缓存（默认 30 秒，`RAG_READY_PROBE_TTL_S`）。可用 `RAG_READY_PROBE_LLM=0` 关闭生成侧校验，但那会让“探针全绿、提问全失败”重新变成可能，不建议。
- 判定口径：连接失败、超时、重定向、鉴权失败（401/403）、额度不足（402）、限流（429）和服务端 5xx 都算不可用。404/405 可能表示兼容服务没有模型清单端点，此时只确认链路可达并跳过模型清单校验。网关错误通常表示系统或环境代理接管了内网流量。
- 就绪结论与提问结果保持一致：`DEEPSEEK_API_KEY` 缺失（含只填了空白）时提问必然以 `llm_auth` 失败，因此探测直接判未就绪并指明该配置项，而不是报成网络故障。key 是否算“已配置”由 `app/llm.py` 的 `effective_api_key()` 单点定义，请求头构造、提问校验与健康探测共用，避免口径不一致。

生产应由 IT 控制管理/诊断入口范围，不把接口文档可见性当成授权。

改 env 不等于改数据库。升级可能涉及模式变化，先读 [备份回退](OPERATIONS.md)，不能把旧程序直接接到未知新库。

## 代理接管：一种会伪装成“模型服务故障”的部署事故

模型服务按设计是本机或内网依赖（`DEEPSEEK_BASE_URL` 多为 `http://127.0.0.1:11434/v1`）。但 Python 的 httpx 默认会读取 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`；在 Windows 上还会经 urllib 读取 WinINET 系统代理（注册表 `Internet Settings`），**并且不读其中的 `ProxyOverride` 绕过列表**。

后果是：即使系统明确写了 `127.*`、`localhost`、`192.168.*` 不走代理，请求仍会被送给代理；代理无法转发本机或内网地址时返回 502。用户看到的是“提问全部失败、模型服务不可用”，而模型服务其实完全正常——排查方向被完全带偏。

本产品因此**默认不使用任何代理**（`RAG_LLM_TRUST_ENV_PROXY=0`），并且就绪探测把 502/503/504 判为不可用而不是“链路可达”，两道措施互为兜底。运维要点：

- 部署机上的代理客户端（Clash、企业安全客户端、VPN 工具等）开启系统代理时，本产品不受影响，无需为其配置绕过规则；这是刻意的设计，不是遗漏。
- 反向代理/负载均衡用 502 表示“后端不可达”时不会被误读成就绪。
- 若某部署确实要经代理访问公网 API，才显式设 `RAG_LLM_TRUST_ENV_PROXY=1`，并自行确认代理能转发到模型地址；此时内网依赖被代理接管的误判风险由该部署自行承担。
- 排查方法：`curl`（会走系统代理）与省略代理的直连结果不一致时，即为此类问题。就绪响应里的 `checks.llm.message` 会直接指出这一点。

## 解析隔离：为什么上传一份坏文件不该影响其他用户

单 worker、单副本是本项目的硬约束（内存向量索引、限流与并发闸门都在进程内），而文档解析在上传请求里**同进程**执行。这意味着：一份畸形文件只要让解析库在解压时把内存吃满，就足以杀掉唯一的进程，**所有在线用户的提问同时中断**，重启后对同一份文件重新索引还会再撞一次。

威胁模型要说清楚：上传权限属于 `kb_admin`，所以这不是"外部攻击者"的边界问题；但**文件内容是外来输入**——设备手册、客户提供的 PDF/DOCX 都来自公司之外，损坏或刻意构造都可能，而管理员无法预先判断。

已有的解析资源上限（解压炸弹体检、累计字符数、单元数、切片数）都作用在**提取之后**，或在交给解析库之前做压缩包层面的体检，管不到解析库内部的内存膨胀。因此解析改在子进程执行，提供三层保护：

- **硬超时**（`RAG_PARSE_TIMEOUT_S`）：超时后终止子进程。
- **内存上限**（`RAG_PARSE_MEMORY_MB`）：POSIX 用 `RLIMIT_AS` 限制子进程地址空间。**Windows 没有等价的地址空间限制能力**，此时只有超时与崩溃隔离生效；这是如实说明的能力差异，不要当成等效实现。企业部署以 Linux 为主，两种保护都具备。
- **崩溃隔离**：子进程段错误、被系统 OOM 杀掉、异常退出，都只表现为一次解析失败。

终止时**杀整棵进程树**：`.doc` 解析会再起 `antiword`，只终止直接子进程会把它变成孤儿继续占内存——本机真实发生过孤儿进程把物理内存吃到 1.4GB、整个应用被换页到无法响应的事故。POSIX 上子进程自成进程组后按组杀，Windows 上用 `taskkill /T`。

失败原因按四类区分，写进文档的失败原因字段，便于管理员分辨"文件坏了"和"服务器资源不够"：

| code | 含义 | 处理方向 |
|---|---|---|
| `parse_timeout` | 超时未完成 | 检查该文件；确认正常后再调大 `RAG_PARSE_TIMEOUT_S` |
| `parse_memory` | 超出内存上限（仅 POSIX 会出现） | 检查该文件；调大前先确认服务器内存足够 |
| `parse_failed` | 解析组件抛出的异常，已转成可读信息 | 文件很可能损坏或格式不兼容；把文件与信息一并反馈 |
| `parse_crashed` | 子进程没留下说明就退出（段错误/被 OOM 杀掉） | 文件很可能损坏；服务器未受影响 |

解析器自己抛出的 `ParseError`（例如"扫描件无文本层"）**原样透传**，message 与 code 与不隔离时逐字一致——隔离不应改变正常业务的错误语义。

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
