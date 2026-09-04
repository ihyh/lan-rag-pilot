# 局域网 RAG 试点系统 —— IT / 管理员交付清单

> 面向公司 IT、网络与系统管理员的中文交付文档。系统版本 v0.1.0；所有变量名、端口、路径与仓库代码一致（配置出处 `app/config.py`、`docker-compose.yml`、`Dockerfile`、`.env.example`）。
> **定位提醒**：这是**隔离局域网内的试点系统**，明文 HTTP、初始弱口令，红线与整改项见 §8。正式发布前必须逐项过门禁。
> 工程根目录：`C:\Users\23960\Desktop\agent\rag`（下文以 `<rag>` 指代）。
> 安全与可维护性复核清单见 `<rag>/docs/SECURITY_AUDIT.md`。

---

## 1. 系统概要（速览）

| 项 | 内容 |
|---|---|
| 形态 | 单体 FastAPI 应用 + SQLite（WAL）+ 本地 BGE 嵌入检索 + DeepSeek API 生成（仅外发问题与 Top-5 检索片段，完整文件永不出内网） |
| 技术栈 | Python 3.12（镜像 `python:3.12-slim`）、CPU 版 PyTorch、`sentence-transformers`、嵌入模型 `BAAI/bge-small-zh-v1.5`（512 维）、Argon2id、pypdf/python-docx |
| 容器 | compose 项目名/容器名 **`rag-pilot`**，服务键 `rag`，镜像 `rag-pilot:local`，`restart: unless-stopped` |
| 端口 | 宿主机 **8088** → 容器 8088（映射可改，见 §4 FAQ 类说明） |
| 数据卷 | `rag-pilot_rag_data` → `/rag/data`（含 `rag.db` 与 `uploads/`）；`rag-pilot_rag_models` → `/rag/models`（模型缓存） |
| 健康检查 | 存活 `/api/health`；Compose readiness 每 30s 请求 `http://127.0.0.1:8088/api/ready`（10s 超时、3 次重试、30s 启动宽限）；模型未就绪时 readiness 为 503 |
| 支持文档格式 | PDF / DOCX / TXT / MD；**无 OCR、无 Excel**；扫描件 PDF 明确报错拒收 |
| 认证 | 账号密码（root/user 两级）+ HttpOnly Cookie 会话（`rag_session`，SameSite=Lax）；**无 SSO** |
| 规模上限 | 试点约 20 人 / 1000 文档 / 5 万切片；之后须迁移 PostgreSQL+pgvector（见 §9） |
| 界面状态 | 服务端页面 `/login /app /admin` 已提供登录、问答、引用、反馈和 root 管理功能 |

## 2. 部署前置条件（□ 勾选核对）

- □ **Docker Engine + Docker Compose** 已安装（`docker --version`、`docker compose version` 可执行）。
- □ 宿主机端口 **8088** 空闲（`netstat -ano | findstr 8088`），或已按 §4.3 改用其它映射。
- □ 磁盘：系统盘 ≥ 10GB（镜像含 CPU PyTorch，体积较大）；数据卷所在盘为长期存储预留空间，SQLite+上传文件按文档量增长（单文件上限 25MB）。
- □ 内存：建议 **≥ 4GB 可用**（嵌入模型加载 + 5 万切片索引约 100MB + 应用）。
- □ 出网策略（试点网络 → 外网）按需放行：

| 目标 | 端口 | 用途 | 首次部署后可否收紧 |
|---|---|---|---|
| `api.deepseek.com` | 443/TCP | 问答时调用 LLM（每次问答都会出网） | 保持放行（功能必需） |
| `huggingface.co` 或 `hf-mirror.com` | 443/TCP | 首次启动自动下载嵌入模型 | 可收紧：完成 §4 离线模型预下载后断网也可运行 |
| `download.pytorch.org` | 443/TCP | 仅 `docker compose build` 时安装 CPU PyTorch | 构建完成后可断 |
| 局域网入站 | 8088/TCP | 员工浏览器访问 | 仅放行目标网段（如 172.16.0.0/16），勿暴露公网 |

- □ 明确**服务不会暴露到公网**，也不在不可信无线网上直接开放（HTTP 明文）。

## 3. 网络与地址规划

### 3.1 DHCP 保留（固定 IP：**172.16.3.50**）

目标：宿主机 MAC 与 IP 绑定，避免重启换 IP 导致员工书签/企业微信链接失效。

1. 登录公司 DHCP 服务器（Windows DHCP 或网关设备）。
2. 新建“保留”：
   - 保留名称：`RAG-PILOT-HOST`（示例）
   - **IP 地址：172.16.3.50**
   - **MAC 地址**：在宿主机执行 `getmac /v` 取以太网适配器 MAC 填入（用小写连字符格式，如 `aa-bb-cc-dd-ee-ff`）
   - 作用域需在 172.16.3.x 网段内且不与其它保留冲突
3. 客户端验证：在宿主机 `ipconfig /renew` 后 `ipconfig` 确认 `IPv4 地址` 为 172.16.3.50。
4. （可选）在网关/防火墙上为 172.16.3.50 建一条仅允许 8088 入站 + 出站白名单的规则。

### 3.2 内部 DNS 记录

在内部 DNS（Windows DNS / 内网域名服务器）建一条 **A 记录**：

- 主机名：`rag`（示例）
- 域名：公司内部域，如 `corp.example.local`
- IP：`172.16.3.50`

验证：内网机器 `nslookup rag.corp.example.local` 应解析到 172.16.3.50。员工与手机即可用 `http://rag.corp.example.local:8088` 访问，域名变更时只需改 DNS。

### 3.3 RAG_PUBLIC_ORIGIN 填写

在 `<rag>/.env` 中把对外访问地址写全（**协议+主机，无末尾斜杠**），例如：

```dotenv
RAG_PUBLIC_ORIGIN=http://rag.corp.example.local:8088
# 无 DNS 时也可直接用 IP：
# RAG_PUBLIC_ORIGIN=http://172.16.3.50:8088
```

用途（来自代码）：启动横幅打印、`GET /api/admin/overview` 返回 `model.public_origin` 供前端/监控核对。注意：**同源校验**（§7.1）以请求自身的 `Host` 与 `Origin/Referer` 比对为准，与 `RAG_PUBLIC_ORIGIN` 无关；因此入口地址必须与员工实际访问的地址一致（经反向代理时需保持 Host 透传）。

## 4. 首次初始化与密码 / 密钥管理

### 4.1 部署步骤（Docker）

```powershell
cd C:\Users\23960\Desktop\agent\rag
Copy-Item .env.example .env        # .env 已被 .gitignore 忽略，勿提交版本库
# 编辑 .env，至少填写/生成三项（详见下节）：
#   DEEPSEEK_API_KEY=sk-...
#   RAG_ROOT_PASSWORD=...
#   RAG_SECRET_KEY=...
docker compose up -d --build
docker compose ps                   # 期望 rag-pilot 处于 Up (healthy)
docker compose logs -f rag          # 首次启动自动下载嵌入模型，观察日志
```

- 首次启动且库为空时，系统用 `RAG_ROOT_PASSWORD` 自动创建 **root** 账号并写审计 `system_init`（幂等；`app/main.py _bootstrap`）。**库空且未设置该变量 → 进程拒绝启动**（错误信息明确提示）。
- 嵌入模型在后台线程加载/下载，**不阻塞启动**：登录与健康检查可用，`model_ready:false` 期间问答/上传返回 503 `embed_not_ready`；日志出现“嵌入模型就绪：BAAI/bge-small-zh-v1.5（512 维）”后即可正常使用。
- 服务器无法访问 Hugging Face 时按 §4.4 离线模型方案。

### 4.2 初始口令与密钥（重要）

- **`root` 初始密码（示例 `fcd123`）只是试点临时弱口令**：该值出现在测试与示例环境（`tests/api_smoke.py` 默认 `RAG_ROOT_PASSWORD` 即 `fcd123`）。正式试点开始前必须换成强口令（≥12 位混合字符），换法：
  - root 登录后调用 `POST /api/me/password`（改自己），或
  - 任一启用的 root 调 `PATCH /api/admin/users/{root_id}`（帮他人重置）。
- **`RAG_SECRET_KEY`**（会话令牌 HMAC 密钥）生成并写入 `.env`：

  ```powershell
  python -c "import secrets;print(secrets.token_urlsafe(48))"
  ```

  不配置时系统回退内置开发密钥 `rag-pilot-dev-key-change-me` 并在启动日志告警（`security.py`）。**正式环境必须配置强密钥**。注意：中途更换密钥会使所有已登录会话失效（全员重新登录）。
- 修改 `.env` 后需 `docker compose up -d` 重建容器生效；`RAG_ROOT_PASSWORD` 仅首次建库有效，之后改它不影响库内密码（详见 README §13 FAQ）。
- 忘记 root 密码：系统**无自助找回**；可行做法（改库或重建）见 README §13 Q3，重建会清空全部数据。

### 4.3 端口 / 卷 / 备份路径速查

- 改宿主机端口：编辑 `docker-compose.yml` 的 `ports`（如 `"9088:8088"`，容器内固定 8088），`docker compose up -d` 生效。
- 数据文件位置（容器内）：`/rag/data/rag.db`、`/rag/data/uploads/`；宿主机侧通过卷访问：`docker volume inspect rag-pilot_rag_data` 查看挂载点，或按 §7.2 用临时容器备份。

### 4.4 离线模型部署（服务器无法访问 Hugging Face 时）

```powershell
# 1) 在有网机器（可与目标机同镜像或本机装好依赖）预下载：
docker compose build
docker compose run --rm rag python scripts/predownload_models.py
# 或：python scripts/predownload_models.py        （本机直跑）
# 国内镜像加速：
$env:HF_ENDPOINT = "https://hf-mirror.com"        # 再执行上面命令

# 2) 将生成的 models 目录整体拷贝到目标服务器，如 C:\rag-models（须含 hub 子目录）

# 3) 目标机新建 docker-compose.override.yml（compose 自动合并，不改原文件）：
#    services:
#      rag:
#        volumes:
#          - rag_data:/rag/data
#          - C:/rag-models:/rag/models

# 4) 重启并确认：
docker compose up -d
docker compose logs -f rag    # 期望：嵌入模型就绪：BAAI/bge-small-zh-v1.5（512 维）
```

> 模型缓存目录在容器内由环境变量固化：`HF_HOME`/`TRANSFORMERS_CACHE`/`HF_HUB_CACHE`/`SENTENCE_TRANSFORMERS_HOME` 均指向 `/rag/models`（`Dockerfile`）；`HF_ENDPOINT` 写入 `.env` 即可经 `env_file` 注入容器被 Hub 客户端读取。
> 注意：`RAG_EMBED_BACKEND=mock` 是测试模式（伪向量、无语义），生产部署**不要**使用。

## 5. 企业微信 / 飞书“工作台应用链接”

**共同前提**：两个入口都只是把**同一个内部 HTTP(S) URL** 挂在应用/工作台上，由客户端内置浏览器打开；**无机器人、无 SSO、无回调**，也不走公网——员工终端必须能直连内网（公司 Wi-Fi 或 VPN）才能访问 172.16.3.50:8088。

- 统一入口 URL（二选一，全司一致）：
  - `http://rag.corp.example.local:8088`（有 DNS，推荐），或
  - `http://172.16.3.50:8088`（无 DNS 时）
- **企业微信**：管理后台 → 应用管理 → 创建自建应用 → 应用主页/可信域名处填上述 URL → 可见范围设为试点团队 → 员工在“工作台”点击打开（企业微信移动端内嵌浏览器直接访问内网地址；若走“企业微信代理/中转”需确认可回源内网，否则失效）。
- **飞书**：开发者后台（或用管理员身份）→ 企业自建应用 → 添加“网页应用” → 网页链接填上述 URL → 可用范围设为试点团队 → 在工作台打开。
- 配置后自测：分别用**企业微信与飞书客户端**打开，确认能完成登录、问答和 root 管理操作且无需公网。若两者入口要求 HTTPS，请先完成 §8 的 HTTPS 改造再配置，避免在明文 HTTP 上做入口推广。

## 6. 手机访问与 PWA 说明（如实）

- 手机（iOS/Android）浏览器可访问 `http://172.16.3.50:8088` 或内网域名，**前提是手机连接公司 Wi-Fi/VPN**（流量走内网，4G/5G 公网无法到达）。
- 页面含移动端 viewport、Web App Manifest 和 Service Worker；Service Worker 只在 HTTPS 下注册，且不会缓存 `/api/`：
  - HTTP 阶段：只能作为**普通网页**使用；浏览器“添加到主屏幕/桌面快捷方式”仅是书签式入口，无离线能力、不满足完整安装条件。
  - HTTPS 阶段：浏览器可注册 Service Worker 并安装为 PWA；离线缓存仅覆盖页面壳和静态资源，问答、登录和管理接口仍必须联网。
- 当前不提供应用商店安装包或 Windows EXE；企业微信、飞书仍采用同一网页入口。

## 7. 日常运维清单

### 7.1 查看状态与日志

```powershell
docker compose ps                       # rag-pilot 应 Up (healthy)
docker compose logs -f rag              # 跟踪日志（容器名 rag-pilot，服务键 rag）
docker compose logs --tail=200 rag      # 最近 200 行
```

日志中值得关注：启动横幅（版本/对外地址/嵌入模型/模型接口）、`嵌入模型就绪…`、`未设置 RAG_SECRET_KEY…` 告警、每次问答的审计（存库而非仅日志）。

### 7.2 备份与恢复（data 卷）

备份对象：**`rag-pilot_rag_data` 卷**（含 SQLite `rag.db`、上传文件 `uploads/`）。模型卷可重建，不必备份。

推荐使用工程内脚本生成归档和 SHA-256 校验文件；脚本不会停止服务，也不会删除或覆盖生产卷：

```bash
cd /home/ihyh/rag-pilot
bash scripts/backup_data.sh /home/ihyh/rag-pilot/backups
```

安装每日 02:30 自动任务（可把第二个参数改成其它 `HH:MM` 时间）：

```bash
bash scripts/install_backup_cron.sh /home/ihyh/rag-pilot/backups 02:30
crontab -l | grep 'rag-pilot daily backup'
```

任务日志写入 `backups/cron.log`。请按公司策略把 `backups/` 复制到独立磁盘或受控存储；本机备份不能替代异地备份。

### 7.2.1 反馈与部门/知识库权限

- 问答页每条本人回答下方可点“有帮助 / 没帮助”；root 在管理页“问答与审计 → 用户反馈”查看。
- 用户反馈面板提供 CSV 导出，可直接整理为后续评测集候选；导出接口仍受 root 会话保护。
- 管理页“部门 / 知识库”可新建部门和知识库；在“用户”列表按部门 ID 分配用户，在“文档”列表按知识库 ID 分配文档。
- 旧数据启动时会自动归入“默认部门 / 默认知识库”；新建用户和上传文档也会先进入默认范围，再由 root 调整。
- user 只会检索自己所属部门下知识库的 ready 文档；root 不受范围限制。

恢复演练只解包到临时目录，并执行 SQLite 完整性检查，不会改动现有 Docker 卷：

```bash
bash scripts/restore_check.sh /home/ihyh/rag-pilot/backups/rag_data_YYYYMMDDTHHMMSSZ.tgz
```

看到 `restore check ok` 后，才说明该归档可用于后续人工恢复。建议用 cron 或公司备份系统每日执行备份，并把 `backups/` 复制到另一块磁盘或受控存储。

```powershell
# 在线热备 SQLite（WAL 模式下安全），先做一致性副本：
docker compose exec rag python -c "import sqlite3; s=sqlite3.connect('/rag/data/rag.db'); d=sqlite3.connect('/rag/data/backup-rag.db'); s.backup(d); d.close(); s.close(); print('backup ok')"

# 把备份与整卷导出到宿主机（示例输出到 D:\backups\rag）：
docker run --rm -v rag-pilot_rag_data:/data -v D:\backups\rag:/backup alpine tar czf /backup/rag_data.tgz -C /data .
```

恢复（整卷回滚）示例：停服 → 把旧卷导出后重建卷并灌回 → 起服：

```powershell
docker compose down
docker volume rm rag-pilot_rag_data
docker volume create rag-pilot_rag_data
docker run --rm -v rag-pilot_rag_data:/data -v D:\backups\rag:/backup alpine tar xzf /backup/rag_data.tgz -C /data
docker compose up -d
```

> 建议备份周期与公司数据策略一致（至少每日一次在线 `sqlite3.backup` + 定期整卷导出异地存放）；恢复演练至少做一次。**任何重大升级前先备份**。

### 7.3 健康检查

```powershell
curl.exe http://127.0.0.1:8088/api/health
# 期望 {"status":"ok","version":"0.1.0","model_ready":true,...}
curl.exe http://127.0.0.1:8088/api/ready
# 期望 HTTP 200 且 {"status":"ready"}；模型加载失败时为 HTTP 503
```

- `status:ok` + `model_ready:true` = 进程存活且嵌入模型正常；`/api/ready` HTTP 200 才表示可接收问答。
- `model_ready:false` = 模型仍加载/已失败（看日志；离线部署核对 §4.4）。
- 可纳入公司监控（如 Zabbix/自有探针）每 1–5 分钟探测一次。

### 7.3.1 HTTPS 反向代理

Ubuntu 上的 Nginx 配置模板见 `deploy/nginx/rag.conf.example`，Compose 绑定模板见 `deploy/docker-compose.https.override.yml.example`。正式接入步骤：申请公司内网证书 → 修改 `server_name` 和证书路径 → 复制 Compose 绑定模板让端口只监听 `127.0.0.1:8088` → Nginx 对内提供 443 → `.env` 设置 `RAG_COOKIE_SECURE=true` 和真实 `RAG_PUBLIC_ORIGIN` → `nginx -t && systemctl reload nginx`。证书、DNS 和防火墙仍需公司环境配置，模板不会自动执行这些操作。

### 7.4 升级步骤（代码更新）

```powershell
cd C:\Users\23960\Desktop\agent\rag
# 1) 备份（§7.2）
# 2) 拉取/更新代码（git pull 等）
# 3) 重新构建并滚动应用：
docker compose build
docker compose up -d          # restart: unless-stopped + 卷保留，容器重建、数据不丢
docker compose ps
docker compose logs -f rag    # 确认启动横幅与模型就绪
# 4) 冒烟回归：见 README §12（或 <rag>\tests\api_smoke.py 说明）
```

- 当前为 v0.1.0 且**无数据库迁移框架**（建表 `CREATE TABLE IF NOT EXISTS`），若未来升级引入表结构变更，必须先备份并按发布说明手工迁移。
- 健康检查由 compose 自动执行（§1），无需人工介入。

## 8. 安全与合规门禁清单（试点前提与红线）

> 本系统按“隔离局域网试点”设计；**以下任一项未满足前，禁止放入敏感/生产资料或扩大使用团队**：

**试点启动前提**
- □ 仅限隔离局域网/测试网段运行，物理/逻辑上与生产网与公网隔离。
- □ 参与人员为内部指定试点团队，账号由 root 统一创建、按人实名（禁止共用账号）。
- □ 已告知参与者：问答内容（问题/答案/引用）会**以明文 HTTP 传输**并记录在案，不得提问敏感数据。

**进入正式使用前必须完成**
- □ **HTTPS**：前置反向代理/网关终结 TLS（如内网 CA 或公司证书），并把 `RAG_COOKIE_SECURE=true` 写入 `.env`；入口地址与 `RAG_PUBLIC_ORIGIN` 一致。
- □ **更换初始弱口令**（root 及所有账号，示例值 `fcd123` 仅用于测试环境）；启用最小可用账号集，长期不用的账号停用（`is_active=false`）。
- □ **配置强 `RAG_SECRET_KEY`**（§4.2），确认启动日志不再出现开发密钥告警。
- □ 复核出网白名单（§2）：除 `api.deepseek.com` 外无多余外联；模型已离线化时可整体断外网。
- □ 端口 8088 仅对需要访问的网段开放。

**日常管理**
- □ 人员变更即日处理：离职/转岗账号停用（`PATCH /api/admin/users/{id}`，`is_active=false`）；root 账号数保持最少（系统强制至少保留 1 个启用 root，且不能停用/降级自己）。
- □ 定期（如每周）查看审计：root 登录后 `GET /api/admin/audit`（或管理端页面）核对异常登录（`login_failed` 集中出现=可能撞库）、`doc_upload`、`llm_query`、`settings_update`；异常行为有 IP 可追（`app/routers/*` 均记录客户端 IP 到 `audit_logs.ip`）。
- □ 数据按公司分类分级管理：本系统不提供敏感级加密存储（SQLite 落盘明文、无字段加密），敏感资料应等 §8 门禁全部通过后再考虑。
- □ 定期备份（§7.2）并至少演练一次恢复。

**已知限制（勿超出使用）**：仅 PDF/DOCX/TXT/MD、无 OCR/Excel；无 SSO/多因素；HTTP 试点级防线（Cookie + 同源校验）；单进程（限流/并发闸门/内存索引在进程内），不得多副本横向扩展。

## 9. 容量与升级路线

- **试点容量上限**（经验目标）：约 **20 人 / 1000 文档 / 5 万切片**。内存向量索引 5 万切片（512 维 float32）约 100MB（`app/index.py` 注释）；SQLite 单写进程在批量并发上传时排队（入库全程 `ingest_lock` 串行）。
- **触发升级的信号**：切片数接近 5 万 / 并发问答排队明显 / 文档管理批量操作变慢 / 需要高可用。
- **升级路线（到 PostgreSQL + pgvector）**：
  1. 向量与 Top-K 检索迁至 PostgreSQL + pgvector（替换内存 numpy 索引 `app/index.py`）；
  2. **多进程改造**：进程内滑动窗口限流与并发闸门（`app/ratelimit.py`）换 Redis 等共享组件；`ingest_lock` 换分布式锁；
  3. 会话（现落 SQLite `sessions`）可平滑迁入同一 PostgreSQL；
  4. 前端页面补齐、HTTPS/SSO 视需要纳入。
- 迁移前备份（§7.2）并按发布说明执行；本仓库未含迁移工具。

## 10. 交接核对表（汇总）

- □ §2 前置条件全部满足（Docker、8088、磁盘/内存、出网策略）
- □ §3 DHCP 保留 172.16.3.50 生效；DNS A 记录可解析；`.env` 已填 `RAG_PUBLIC_ORIGIN`
- □ §4 `.env` 三件套（`DEEPSEEK_API_KEY`/`RAG_ROOT_PASSWORD`/`RAG_SECRET_KEY`）就位；`docker compose up -d` 后 healthy；root 可登录；模型就绪
- □ 无法访问 HF 时已完成 §4.4 离线模型挂载并验证日志
- □ §8 门禁：是否已 HTTPS / 已更换弱口令 / 已配强密钥 / 出网白名单核对（未完成则系统仅限隔离试点用途并书面知会）
- □ §7 备份任务已配置并演练过一次恢复
- □ 企业微信 / 飞书入口按 §5 配置并由试点用户实际点击验证
- □ 交付人签字：____________　接收人（IT）签字：____________　日期：____________

---

> 开发者向：完整环境变量表、API 一览、权限模型、表结构、FAQ 见仓库根目录 `README.md`。
