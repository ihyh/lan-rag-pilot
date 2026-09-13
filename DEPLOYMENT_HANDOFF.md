# RAG Ubuntu Docker 部署交接记录

> **历史归档，非现行部署教程。** 以下地址、版本、测试结果和在线操作仅记录当时实验，不代表当前服务器状态，也不适用于严格离线安装。新部署请使用 [Ubuntu 离线指南](docs/UBUNTU.md)、[安全门槛](docs/SECURITY.md)和[运维指南](docs/OPERATIONS.md)。本次仅标记归档，未重新验证或部署下述主机。

更新时间：2026-09-09（Asia/Shanghai）

> 当前结论：应用提交 `2631f8e` 已部署到 Ubuntu，且对应功能已通过 PR #4 合并到 GitHub `main`。本页后半部分保留 2026-09-05 初次部署与迁移过程作为历史记录；判断当前状态时，以本节的 2026-09-09 实时检查为准。

## 0. 2026-09-09 实时运行状态

- Ubuntu 容器 `rag-pilot` 已连续运行约 13 小时，状态 `running healthy`，自动重启策略为 `unless-stopped`。
- 当前镜像 ID 为 `6876d37067be`，镜像标签中的源码版本为 `2631f8e`；回退标签 `rag-pilot:before-2631f8e` 指向上一版 `27a34d4` 镜像。
- SQLite `integrity_check=ok`；当前共有 3 个用户、20 个文档、24112 个切片、44 个对话、44 条问答、185 条引用、42 条反馈、164 条审计和 20 个上传文件。
- 嵌入模型为 `BAAI/bge-small-zh-v1.5`，生成模型为内网 Ollama `qwen3:1.7b`；模型接口返回 HTTP 200，配置模型存在。
- `127.0.0.1:8090`、`192.168.136.128:8088` 及当前 WLAN 地址 `172.16.3.56:8088` 的 `/api/health`、`/api/ready` 均返回 HTTP 200。
- 原计划地址 `172.16.3.50` 已因 DHCP 变化失效；Ubuntu 的 `RAG_PUBLIC_ORIGIN` 仍显示旧地址，属于待整改配置。正式团队入口必须先做 DHCP 保留或内部 DNS，不应长期使用当前临时地址 `172.16.3.56`。
- 每日 02:30 数据备份任务仍在 crontab 中，服务器目前保留 4 个数据备份归档；本次只检查任务和文件数量，未打开备份内容。

## 0.1 2026-09-05 本地离线模型历史

- Windows 已安装 Ollama `0.33.3`，模型目录为 `D:\Ollama\models`，只监听 `192.168.136.1:11434`。
- 已把安装器的托盘自启动项改为隐藏运行纯 `ollama serve`，启动命令固定 `OLLAMA_NO_CLOUD=1`、`OLLAMA_CONTEXT_LENGTH=8192`，且不继承 HTTP(S) 代理。
- `qwen3:4b` 已下载并记录摘要，但 8192 上下文加载后宿主机内存连续为 94.6%–95.2%，按验收规则已判定不适合这台主机。详情见 `deploy/MODEL_MANIFEST.md`。
- 目标已降为 `qwen3:1.7b` 并完成官方权重校验。一次预热后，3 次模拟 Top-5 非敏感问答分别耗时 12.04s、17.69s、15.06s，宿主机内存稳定为 84.7%–85.0%；连通与性能门槛通过。
- 正式质量尚未通过：30 条以上人工核对问题/答案/页码仍未提供，且模拟测试发现一条回答含片段之外的泛化说明，必须依赖真实评测逐条复核忠实度。
- Windows WLAN 地址 `172.16.3.50:11434` 已实测无法连接；但安装器生成的两条 `ollama.exe` Public 入站规则过宽，当前非管理员会话无法修改。管理员 PowerShell 应执行：

```powershell
Get-NetFirewallRule -DisplayName 'ollama.exe' | Disable-NetFirewallRule
Get-NetFirewallRule -DisplayName 'RAG Ollama from Ubuntu VM' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'RAG Ollama from Ubuntu VM' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress 192.168.136.1 -LocalPort 11434 -RemoteAddress 192.168.136.128 -Profile Any
```

- Ubuntu 已授权当前 Windows SSH 公钥；升级前备份、02:30 cron、源码重建和 Ollama `.env` 切换均已完成。

### 0.2 统一数据库迁移历史

- 唯一活动数据源：Ubuntu Docker 卷 `rag-pilot_rag_data`。Windows 原 `data/rag.db` 与上传文件仅作为停用恢复副本保留，不再启动本地 RAG 应用读取它们。
- 2026-09-05 已将 Windows 数据一致性快照恢复到该卷：2 个用户、21 个文档、25217 个切片、2 条问答、10 条引用、2 条反馈、46 条审计、21 个上传文件；SQLite `integrity_check=ok`，无缺失存储文件。
- 迁移包：`rag-windows-main-24f6135.tgz`，SHA-256 `769ec1ee4789837fd4730348d9da79d1bd5e37ce59980d9db1e9371322eef5bf`；传输前后校验及 `restore_check.sh` 均通过。
- 切换前 Ubuntu 回退包：`backups/rag_data_20260905T133511Z.tgz`，保留原 1 个文档、4 条问答和 21 条审计；校验与临时恢复检查均通过。
- 当日重启后容器状态为 `running healthy`，当时的 `172.16.3.50:8088/api/health` 与 `/api/ready` 均返回 HTTP 200。
- 当日外部入口使用 `172.16.3.50:8088`；本机入口配置为 `127.0.0.1:8090` 转发到 `192.168.136.128:8088`。这两种地址的 Cookie 相互独立，但后端数据库相同。该段是迁移完成时的历史快照，不代表当前文档和问答数量。

## 1. 当前结论

当前 RAG 已部署到本机 VMware Ubuntu 虚拟机。应用、多轮对话、设备/文档范围、混合检索、DOC 上传和本地 Ollama 调用均已进入运行镜像；容器、模型、数据库完整性和重启持久化已验证。

- 宿主机直连地址：`http://192.168.136.128:8088`
- 本机入口：`http://127.0.0.1:8090`
- 临时局域网入口：`http://172.16.3.56:8088`（DHCP 地址，可能再次变化）
- 计划固定入口：`http://172.16.3.50:8088`（当前未生效，不应继续作为书签）
- 容器：`rag-pilot`
- 镜像：`rag-pilot:local`
- Compose 项目：`rag-pilot`
- 容器状态：`healthy`
- 自动重启：`unless-stopped`
- Ubuntu 项目目录：`/home/ihyh/rag-pilot`
- Windows 源码目录：`C:\Users\23960\Desktop\agent\rag`

VM 使用 VMware NAT；Windows 的 `0.0.0.0:8088` 端口转发仍指向 Ubuntu。由于 WLAN 地址会随 DHCP 变化，当前入口跟随宿主机地址变为 `http://172.16.3.56:8088`。团队使用前应由 IT 配置 DHCP 保留和内部 DNS，再把 Ubuntu 的 `RAG_PUBLIC_ORIGIN` 改为固定地址并重建容器。

## 2. 目标机信息

- VMware VMX：`C:\Users\23960\Documents\Virtual Machines\Ubuntu 64 位\Ubuntu 64 位.vmx`
- VMware 程序：`D:\vmware.exe`、`D:\vmrun.exe`
- Ubuntu：26.04 LTS，x86_64
- 资源：4 vCPU、约 7.2 GiB RAM、约 3.3 GiB swap
- Ubuntu 用户：`ihyh`
- Docker：29.1.3
- Docker Compose：2.40.3
- `ihyh` 已加入 `docker` 组，可不使用 sudo 运行 Docker
- VM NAT 地址：`192.168.136.128`
- Windows WLAN 当前地址：`172.16.3.56/22`（2026-09-09 实测；计划保留的 `172.16.3.50` 尚未生效）
- 根分区：已从 20 GiB 扩到 40 GiB；扩容后剩余约 21 GiB（约 47% 已用）

本次已完成磁盘扩容：VMware 虚拟磁盘、`/dev/sda2` 和 ext4 根文件系统均为 40 GiB。

## 3. 已执行的工作

1. 确认现有 Ubuntu VM、NAT 网络、SSH 地址和用户。
2. 生成一次性 SSH 部署密钥，并由用户把公钥临时加入 `~/.ssh/authorized_keys`。
3. 验证 Docker/Compose 服务，用户执行 `sudo usermod -aG docker ihyh` 后重新登录，确认组权限生效。
4. 检查 Docker 环境为空，没有覆盖或删除原有容器、镜像或卷。
5. 从 Windows 打包并传输运行代码、`.env`、SQLite 数据、上传文档和 BGE 模型缓存；传输包 SHA-256 两端一致：
   `8d5ffae77b59ba0a30eb7a7a115d2327c05f76319c5267ce12430de0b1bd3fcc`
6. 服务器端 `.env` 权限设为 `600`；只校验了 DeepSeek Key 和会话密钥存在，没有输出秘密值。
7. Docker Hub 在当前网络被错误解析到不可访问地址，构建失败。将 Dockerfile 的基础镜像从 `python:3.12-slim` 改为：
   `public.ecr.aws/docker/library/python:3.12-slim`
8. 成功构建 `rag-pilot:local`。构建产生的约 1.891 GB 临时缓存导致磁盘满，随后只执行 `docker builder prune --all --force` 清理本次可重建缓存；应用镜像和数据均保留。
9. 创建命名卷：
   - `rag-pilot_rag_data`
   - `rag-pilot_rag_models`
10. 迁移并校验 SQLite 数据：`PRAGMA integrity_check = ok`。
11. 迁移约 93 MB 的 `BAAI/bge-small-zh-v1.5` 模型缓存。
12. 强制离线加载模型验证成功，维度为 512。Compose 新增：
   - `HF_HUB_OFFLINE: "1"`
   - `TRANSFORMERS_OFFLINE: "1"`
13. 启动容器并完成真实 DeepSeek 问答测试。
14. 重启容器后再次验证模型、登录、文档和聊天历史，确认命名卷持久化。
15. 删除服务器项目目录中迁移用的 `data/`、`models/` 重复副本，正式数据只保存在 Docker 命名卷中。
16. 将 VMware 虚拟磁盘增加 20 GiB，并在 Ubuntu 内执行 `growpart /dev/sda 2` 与 `resize2fs /dev/sda2`；扩容后根分区可用约 21 GiB。

## 4. 已验证结果

| 检查项 | 结果 |
|---|---|
| `GET /api/health` | localhost、VM 直连、当前 WLAN 入口均为 HTTP 200 |
| `GET /api/ready` | localhost、VM 直连、当前 WLAN 入口均为 HTTP 200 |
| embedding | `BAAI/bge-small-zh-v1.5`，ready，512 维 |
| Ollama | `qwen3:1.7b`，接口 HTTP 200，配置模型存在 |
| SQLite 完整性 | `ok` |
| 用户 / 文档 / 切片 | 3 / 20 / 24112 |
| 对话 / 问答 / 引用 / 反馈 | 44 / 44 / 185 / 42 |
| 上传文件 | 20，均位于活动数据卷 |
| 容器健康状态 | `healthy` |
| 自动重启策略 | `unless-stopped` |
| 部署提交 | `2631f8e` |
| 当前镜像 | `rag-pilot:local` / `6876d37067be` |
| 回退镜像 | `rag-pilot:before-2631f8e` / `5b36b5260c37`（应用提交 `27a34d4`） |
| 自动备份 | 当前用户 crontab 每日 02:30 执行 |

本次实时检查没有登录用户界面、读取业务文档、输出问答内容或显示任何密码、密钥。

## 5. 本次修改的源码配置

### `Dockerfile`

基础镜像改为 AWS Public ECR 中的 Docker 官方 Python 镜像缓存，以绕过当前网络的 Docker Hub DNS 问题：

```dockerfile
FROM public.ecr.aws/docker/library/python:3.12-slim
```

### `docker-compose.yml`

增加离线模式，确保已经迁移的本地模型不会在每次启动时等待 Hugging Face 网络检查：

```yaml
HF_HUB_OFFLINE: "1"
TRANSFORMERS_OFFLINE: "1"
```

服务器 `.env` 当前仍保留下面的旧地址：

```text
http://172.16.3.50:8088
```

该值只用于状态展示，不决定实际转发；由于宿主机当前地址已变为 `172.16.3.56`，应在 IT 确定固定 IP 或内部域名后更新它并重建容器。

## 6. 局域网入口（转发正常，固定地址待配置）

Windows 管理员 PowerShell 已执行：

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=8088 connectaddress=192.168.136.128 connectport=8088
New-NetFirewallRule -DisplayName "RAG Pilot 8088" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8088 -Profile Domain,Private
```

确认规则：

```powershell
netsh interface portproxy show all
Get-NetFirewallRule -DisplayName "RAG Pilot 8088"
```

2026-09-05 曾在 Ubuntu `/home/ihyh/rag-pilot/.env` 中把：

```text
RAG_PUBLIC_ORIGIN=http://192.168.136.128:8088
```

改成：

```text
RAG_PUBLIC_ORIGIN=http://172.16.3.50:8088
```

并重建容器配置。该地址当时可用，但 2026-09-09 已因 DHCP 变化失效。当前临时地址为 `http://172.16.3.56:8088`，本机实时检查通过；仍需从另一台局域网设备做最终验收。

固定 IP 或内部 DNS 确定后，在 `.env` 写入最终入口并重建容器：

```bash
cd /home/ihyh/rag-pilot
docker compose up -d --force-recreate
docker compose ps
```

不要把 `172.16.3.56` 当作长期入口。宿主机 IP 改变后，客户端书签和工作台链接都会失效；正式使用应配置 DHCP 保留并优先使用内部 DNS 名称。

如需撤销 Windows 转发：

```powershell
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=8088
Remove-NetFirewallRule -DisplayName "RAG Pilot 8088"
```

## 7. 企业微信 / 飞书入口（等待固定地址）

两个平台最终应统一使用同一个固定入口：

```text
https://<内网域名>
```

接入方式是工作台网页链接，不是机器人或单点登录。在固定 IP、内部 DNS 和 HTTPS 完成前，不要把当前 DHCP 地址保存为全员入口：

- 企业微信：管理后台 → 应用管理 → 创建自建应用 → 应用主页 → 设置上述 URL → 设置可见范围。
- 飞书：开放平台 → 企业自建应用 → 添加网页入口/应用主页 → 设置上述 URL → 设置可用范围并发布。

详细操作和验收清单见 [`docs/WORKBENCH_INTEGRATION.md`](docs/WORKBENCH_INTEGRATION.md)。若任一平台拒绝 HTTP 入口或要求 HTTPS，应停止继续配置，后续先部署 HTTPS。

## 8. 日常运维命令

登录 Ubuntu 后：

```bash
cd /home/ihyh/rag-pilot
docker compose ps
docker compose logs --tail 100
docker compose restart
docker compose up -d
docker compose down
df -h /
docker system df
```

健康检查（Ubuntu 没有安装 curl，可使用 Python）：

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=10).read().decode())"
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/api/ready', timeout=10).read().decode())"
```

重新构建：

```bash
cd /home/ihyh/rag-pilot
docker compose build
docker compose up -d
```

当前扩容后约有 21 GiB 可用空间，可以继续小规模构建和上传；仍应定期检查磁盘，不要删除 `rag-pilot_rag_data` 或 `rag-pilot_rag_models`。

自动备份与安全恢复检查：

```bash
cd /home/ihyh/rag-pilot
bash scripts/install_backup_cron.sh /home/ihyh/rag-pilot/backups 02:30
bash scripts/backup_data.sh /home/ihyh/rag-pilot/backups
bash scripts/restore_check.sh /home/ihyh/rag-pilot/backups/rag_data_*.tgz
```

`restore_check.sh` 只使用临时目录，不会覆盖现有数据卷。生成的 `.tgz` 和 `.sha256` 应复制到独立磁盘或公司受控备份位置。

## 9. 密钥与安全事项

- 当前目标 `.env` 使用非秘密占位值 `DEEPSEEK_API_KEY=ollama`，服务器不得保留有效公网模型 Key；会话密钥和 root 初始密码只保存在 `.env`，本文不记录明文。
- 服务器 `.env` 权限为 `600`。
- 当前为 HTTP，只适用于隔离局域网试点；敏感资料和扩大使用人数前必须启用 HTTPS，并更换初始弱密码。
- 临时 SSH 公钥 `rag-deploy-temporary` 已从 `/home/ihyh/.ssh/authorized_keys` 撤销并验证不存在。
- Windows 临时文件包括：
  - `C:\Users\23960\AppData\Local\Temp\rag-deploy-key`
  - `C:\Users\23960\AppData\Local\Temp\rag-deploy-key.pub`
  - `C:\Users\23960\AppData\Local\Temp\rag-deploy-20260904.tgz`
扩容私钥、公钥和部署压缩包的原路径均已验证不存在；压缩包已移入 Windows 回收站（清空回收站后才会永久删除）。

## 10. 接手时的第一组检查

```bash
cd /home/ihyh/rag-pilot
docker compose ps
docker compose logs --tail 100
df -h /
docker system df
```

2026-09-09 当前 `rag-pilot` 为 `healthy`，端口为 `0.0.0.0:8088->8088/tcp`。本机可用 `http://127.0.0.1:8090`，也可用 `http://192.168.136.128:8088` 直连 VM；当前临时 WLAN 入口是 `http://172.16.3.56:8088`，仅供固定 IP 完成前测试。
