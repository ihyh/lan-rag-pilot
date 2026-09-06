# RAG Ubuntu Docker 部署交接记录

更新时间：2026-09-05（Asia/Shanghai）

> 说明：本文第 1–4 节是此前 Ubuntu 已验证的历史快照。2026-09-05 本地版本已按用户要求取消部门隔离：登录用户共享全部文档，文档管理员管理全部文档，个人问答仍仅本人及 root 可见。部门/知识库接口已移除，历史分类数据保留。此版本尚未在 Ubuntu 重新构建验证；同步最新源码后按第 8 节重新验收。

## 0. 2026-09-05 本地离线模型进展

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

- Ubuntu 的当前 Windows SSH 公钥仍未获授权，因此升级前备份、02:30 cron、源码重建和 Ollama `.env` 切换尚未执行。不得在缺少这些证据时导入公司机密文档。

## 1. 当前结论

此前版本的 RAG 已成功部署到本机 VMware Ubuntu 虚拟机，容器、模型、数据库、真实 DeepSeek 问答和容器重启后的数据持久化均已验证。

- 宿主机直连地址：`http://192.168.136.128:8088`
- 局域网入口：`http://172.16.3.50:8088`
- 容器：`rag-pilot`
- 镜像：`rag-pilot:local`
- Compose 项目：`rag-pilot`
- 容器状态：`healthy`
- 自动重启：`unless-stopped`
- Ubuntu 项目目录：`/home/ihyh/rag-pilot`
- Windows 源码目录：`C:\Users\23960\Desktop\agent\rag`

VM 使用 VMware NAT；已在 Windows 配置端口转发和入站防火墙规则，因此局域网入口为 `http://172.16.3.50:8088`。目前已从宿主机验证入口；仍建议用另一台公司电脑或手机 Wi-Fi/VPN 做最终访问验证。

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
- Windows WLAN 地址：`172.16.3.50/22`
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
| `GET /api/health` | `status=ok` |
| `GET /api/ready` | 需在本轮源码重建后验证；模型就绪时应为 HTTP 200 |
| embedding | `ready`，512 维 |
| 宿主机访问 VM:8088 | 成功 |
| 宿主机访问 LAN 入口 172.16.3.50:8088 | 成功 |
| root 登录 | 成功，角色 `root` |
| SQLite 完整性 | `ok` |
| 用户数量 | 2 |
| 文档数量 | 1 |
| 切片数量 | 2035 |
| 真实 RAG 问答 | 成功，返回 5 个来源 |
| 新问答 chat_id | 3 |
| 容器重启后文档 | 1 |
| 容器重启后聊天 | 2 |
| 容器健康状态 | `healthy` |
| 自动重启策略 | `unless-stopped` |

真实测试问题为“这份手册主要介绍什么内容？请根据文档简要回答。”，调用成功并返回 `Fortrend PLUS-500 SECS manual` 的命中文档片段。未在本文记录 DeepSeek API Key、会话密钥或密码明文。

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

服务器 `.env` 当前的 `RAG_PUBLIC_ORIGIN` 是：

```text
http://172.16.3.50:8088
```

## 6. 局域网入口（已配置）

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

随后已在 Ubuntu `/home/ihyh/rag-pilot/.env` 中把：

```text
RAG_PUBLIC_ORIGIN=http://192.168.136.128:8088
```

改成：

```text
RAG_PUBLIC_ORIGIN=http://172.16.3.50:8088
```

并重建容器配置：

```bash
cd /home/ihyh/rag-pilot
docker compose up -d --force-recreate
docker compose ps
```

此后公司局域网电脑访问：`http://172.16.3.50:8088`。宿主机 IP 改变后，需要同步更新端口入口、`RAG_PUBLIC_ORIGIN` 和内部 DNS；正式使用应让 IT 给 `172.16.3.50` 做 DHCP 保留。

如需撤销 Windows 转发：

```powershell
netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=8088
Remove-NetFirewallRule -DisplayName "RAG Pilot 8088"
```

## 7. 企业微信 / 飞书入口（已准备，需管理员后台保存）

两个平台统一使用下面的网页入口：

```text
http://172.16.3.50:8088
```

接入方式是工作台网页链接，不是机器人或单点登录：

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

同步本轮源码并执行 `docker compose up -d --build` 后，预期看到 `rag-pilot` 为 `healthy`、端口为 `0.0.0.0:8088->8088/tcp`。浏览器可用 `http://192.168.136.128:8088` 直连 VM，或用 `http://172.16.3.50:8088` 通过 Windows 局域网入口访问。
