# 日常运维、备份、升级与迁移

[返回首页](../README.md)

操作前确认你所在的是目标部署目录，知道数据实际路径/卷名，并有可恢复副本。以下示例对应 [Windows](WINDOWS.md) 的 C:\rag 和 [Ubuntu](UBUNTU.md) 的 /opt/rag。不要在未知实例上照抄恢复命令。

## 启停与重启

| 场景 | 操作与成功标准 |
|---|---|
| Windows 启动 | 先启动同用户 Ollama，再运行 start_local.ps1 并明确 -Python；ready 后实际问答 |
| Windows 停止 | RAG 窗口 Ctrl+C 等进程退出；Ollama 单独退出托盘 |
| Ubuntu 临时停止 | 在 /opt/rag 执行 docker compose stop，保留数据卷 |
| Ubuntu 恢复 | docker compose up -d --pull never --no-build；检查 ps、ready、HTTPS 与问答 |
| 只重启现有进程 | docker compose restart rag，不会读取新的容器环境 |
| 改过 .env/镜像 | docker compose up -d --pull never --no-build 使新配置生效 |

Ubuntu 主机重启依赖 Docker/Nginx 自启动与容器 restart 策略；如果之前手动 stop 了容器，应显式 up。Windows 教程不自动安装服务，登录后需启动 Ollama 和 RAG。

不要执行 `docker compose down -v` 或清理业务卷。不是所有历史部署都使用同样卷名，先核实最终 Compose 配置。备份脚本依赖默认 Compose 文件解析；本文部署副本使用 docker-compose.yml。若自定义文件名，需要同步安排脚本/计划任务的 COMPOSE_FILE 环境，不能假定 cron 继承终端变量。

## 应当备份什么

- data：rag.db、uploads 原文件；包含用户、密码哈希、会话、对话、反馈、审计、切片与向量。
- 配置：.env、部署 Compose、Nginx 配置、内部证书及必要私钥（按密钥制度单独保管）。
- 模型：精确的嵌入模型、Ollama 模型和摘要，不能只记一个可变标签。
- 程序：与数据匹配的离线镜像/源码、依赖锁、版本清单。

数据库和文件是一组，不能只备份其中之一。模型不必每天重复拷贝，但每个仍可能恢复的版本必须有可验证副本。备份含敏感信息，需加密介质、限制账号、与服务器分开保存；至少有一份离线或不可被服务账号改写的副本。保留期限和恢复目标由业务/IT 确认，不能无限保留。

## Windows：停机备份

先通知用户停止操作，Ctrl+C 正常停止 RAG，确认无残留应用写入进程。使用有足够空间、已加密的 E:\rag-backups（路径由管理员确认），PowerShell：

```powershell
$backupRoot = 'E:\rag-backups'
$backupRun = Join-Path $backupRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $backupRun -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath C:\rag\data -Destination $backupRun -Recurse -ErrorAction Stop
Copy-Item -LiteralPath C:\rag\.env -Destination $backupRun -ErrorAction Stop
Get-ChildItem -LiteralPath (Join-Path $backupRun 'data') -Force
```

复制完整 data，不能在服务运行时只复制 rag.db；若有 WAL/SHM 也随完整目录保留。密钥配置另按组织要求加密，不把副本放公共共享盘。另记录对应模型/源码包摘要。

备份后重启旧服务。恢复演练使用全新 C:\rag-restore 目录、独立 data、8089 回环端口和相同模型版本；把备份 data 复制到空目标，调整 .env 绝对路径后启动。不要覆盖 C:\rag\data，也不要让两个进程写同一数据库。

## Ubuntu：使用现有备份脚本

脚本建立 SQLite 一致性副本，再打包 uploads；**两者不是同一瞬间的快照**。须安排维护窗口，禁止上传、删除、重处理；最稳妥的是临时封闭客户端入口并确认没有后台入库任务。容器本身需运行才能使用脚本。

在 /opt/rag 执行（/srv/rag-backups 应是已准备的受限备份存储）：

```bash
cd /opt/rag
bash scripts/backup_data.sh /srv/rag-backups
```

输出一个 rag_data_时间.tgz 和对应 sha256 文件。将真实文件名替换下列占位；不要把示例当作已经存在的备份：

```bash
sha256sum -c /srv/rag-backups/rag_data_REPLACE_TIMESTAMP.tgz.sha256
bash scripts/restore_check.sh /srv/rag-backups/rag_data_REPLACE_TIMESTAMP.tgz
```

成功标准：校验 OK、restore check ok，用户/文档/切片数量符合预期。sha256 文件保存的是备份时绝对路径；跨机转移后可直接计算归档 hash 与可信记录比对，不能仅因旧路径不存在判断文件损坏。

restore_check 只在临时目录解包并检查数据库完整性及数量，不执行正式恢复，不验证每份原文件或完整问答。备份脚本不含 .env/模型，必须另行保存。不同时运行两个备份任务，它们使用相同容器临时路径。

计划任务可在管理员确认维护窗口后安装：

```bash
cd /opt/rag
bash scripts/install_backup_cron.sh /srv/rag-backups 02:30
crontab -l
```

这会修改当前账号的 crontab，时间使用主机本地时区；账号需具备 Docker 权限。检查首次实际运行、磁盘容量和日志；脚本不提供自动保留清理、加密或写入冻结。不能仅安装 cron 就声称备份合格。

## 恢复到新 Ubuntu 实例

先通过上一节的校验；只接受可信备份。用新部署目录 /opt/rag-restore、独立 Compose 项目与回环端口 8089，不切换正式入口。

1. 按离线安装准备同版本镜像与模型，不复用可写的生产数据卷。
2. 确认 rag-restore_data 卷不存在，才执行下面命令。
3. 修改恢复副本的 Compose：name 改为 rag-restore，rag_data 的外部 name 改为 rag-restore_data，端口改为 127.0.0.1:8089:8088；其他卷使用另行导入的新卷，或经过确认的只读模型副本。不要沿用硬编码的生产外部卷名。
4. 配置独立的受控 HTTPS 测试入口，保留正确 Secure Cookie 设置，不向其他人开放恢复库。

把下面文件名替换为已校验归档后执行：

```bash
docker volume create rag-restore_data
docker run --rm --network none -v rag-restore_data:/target -v /srv/rag-backups:/backup:ro rag-pilot:offline tar -xzf /backup/rag_data_REPLACE_TIMESTAMP.tgz -C /target
cd /opt/rag-restore
docker compose config --quiet
docker compose up -d --pull never --no-build
```

数据卷根目录应直接包含 rag.db 和 uploads。卷已有内容时停止，不覆盖。必须先跑 restore_check 防止不可信归档路径/链接。

恢复通过标准：ready、登录、用户角色、文档数量及状态、随机原文下载、旧引用、一次新问答均正常。重新导入/换嵌入的情况不属于原样恢复，历史引用需另行处理。测试产生的新问答不能误当备份原数据。

恢复包含旧会话记录；若因泄露/账号事件恢复，须由管理员安排失效会话和凭据轮换。SECRET 改变会使旧会话失效，但不等于重置账号密码。

## Windows 与 Ubuntu 相互迁移

先在旧机停应用、冻结业务，完整备份。SQLite 与原文可迁移，Python 虚拟环境和 OS 安装组件不能跨平台复制。

| 项目 | Windows → Ubuntu | Ubuntu → Windows |
|---|---|---|
| 数据 | 将停机 data 制成可信归档，导入全新数据卷 | 将已校验备份解压到全新 data 目录 |
| 嵌入模型 | 完整目录导入模型卷，保持同一 revision | 完整目录放 Windows 本地路径 |
| Ollama | 使用匹配版本验收的模型包，确认模型 ID | 退出 Ollama，导入新模型目录并重启 |
| 路径/地址 | 改为 /rag/data、/rag/models 及容器服务名 | 改为本地绝对路径及 127.0.0.1 |
| 环境 | 离线导入 Linux 镜像 | 重建 Windows venv，离线装 wheels |

用户、对话与引用是否保留取决于完整数据与精确模型的一致性，不是仅搬运原文。先在新实例验收；切入口时确保只有一处生产写入。旧实例保留但不得继续接收业务，以便回退而不产生双写。

## 升级与回退

1. 准备机审核新版本，生成新离线包；运行机不能 git pull、pip 在线升级或拉模型。
2. 先冻结并备份，记录当前模型/镜像/配置。新包放独立目录，不解压覆盖生产。
3. 用备份数据副本做升级演练，检查是否有数据库迁移及可逆性，跑业务问题与权限回归。
4. 维护窗口中停旧写入，将新实例验收后切入口。新版本拒绝通过时保持旧实例。
5. 回退时恢复“旧代码/镜像 + 升级前数据 + 旧模型/配置”完整组合；不要用旧代码直接打开新版本改过的数据库。切换后新增数据如何处理必须事先约定。

无论升级成功与否，都不自动删除旧数据卷。保留/销毁由管理员按数据制度确认。

## 监控与事件

定期检查 ready、真实问答、磁盘、失败入库、模型延迟、备份时间、证书有效期、账号和审计。日志可能包含路径、用户名和问题，禁止上传到公网日志平台。

出现疑似泄露先限制入口与相关账号，保全证据并通过内部安全渠道处理；不要为“恢复正常”清空审计或删除数据库。
