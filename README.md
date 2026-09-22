# 局域网知识库助手（LAN RAG Pilot）

[中文](README.md) | [English](README.en.md)

上传文档后，用户选择设备并提问；应用检索相关文档片段，调用本地 Ollama 生成回答并显示引用。运行组件是 RAG 应用（FastAPI、SQLite）、BGE 检索模型和 Ollama 生成模型。

| 使用方式 | 服务运行位置 | 用户如何访问 |
| --- | --- | --- |
| Windows 个人知识库 | 三个组件都在自己的 Windows 电脑 | 本机浏览器 `http://127.0.0.1:8088` |
| Linux 企业知识库 | 三个组件都在 Linux 服务器，不依赖 Windows 上的模型或程序 | 员工访问服务器的固定内网 HTTPS 域名 |

源码仓库不包含模型、密码或业务文档。Windows 个人版与 Linux 企业版使用各自的数据、BGE 和 Ollama；两边不会自动同步资料。Windows 和 Linux 联网安装脚本会在首次运行时下载依赖和模型，正式断网服务器则由 IT 导入已核验的离线包。

`192.168.136.128` 只是 VMware 测试虚拟机曾使用的地址，不是项目固定网址。部署到公司 Linux 服务器后，应使用该服务器的实际内网 IP；完成域名和 HTTPS 配置后，员工只使用 IT 提供的内部网址。

## Windows：个人知识库

### 1. 准备软件

安装 [Git for Windows](https://git-scm.com/download/win) 和 [Ollama](https://ollama.com/download/windows)，然后从开始菜单启动 Ollama。

### 2. 首次安装

打开 PowerShell，执行以下三条命令：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
Set-Location C:\rag
.\setup_windows.cmd
```

首次安装需要下载较大的依赖和模型，请保持 PowerShell 窗口打开并等待脚本完成。再次运行同一命令时，脚本会复用已经下载的内容。

### 3. 等待脚本完成

脚本会自动完成以下操作：

1. 如果没有 Python 3.12，通过 Windows Python Installation Manager 或 `winget` 安装。
2. 创建 `.venv`，安装并校验 Python 依赖。
3. 拉取 `qwen3:1.7b`。
4. 从 [GitHub Release](https://github.com/ihyh/lan-rag-pilot/releases/tag/bge-small-zh-v1.5-7999e1d) 下载并校验 BGE。
5. 生成 `.env` 和随机初始密码；已有 `.env` 不会被覆盖。
6. 启动服务。

### 4. 确认安装成功

终端显示服务启动完成后，不要关闭窗口。记下窗口中的 `root` 初始密码和访问网址，通常为 [http://127.0.0.1:8088](http://127.0.0.1:8088)。

在浏览器中打开网址，使用 `root` 和初始密码登录。上传一份测试资料并提问；能够看到生成的回答和引用来源，才表示安装及完整问答流程可用。停止服务时，在运行窗口按 Ctrl+C。

### 5. 后续启动与更新

如果 `C:\rag` 已经存在，不要再次执行 `git clone`。进入该目录，需要更新时先运行 `git pull --ff-only`，然后运行 `setup_windows.cmd`。以后启动也运行 `setup_windows.cmd`，并保持 Ollama 已启动。

IDE 解释器是 `C:\rag\.venv\Scripts\python.exe`。离线安装、手动配置及故障处理见 [Windows 指南](docs/WINDOWS.md)和[故障排查](docs/TROUBLESHOOTING.md)。

## Linux：企业知识库

RAG 应用、SQLite 数据库、BGE 检索模型和 Ollama 生成模型都运行在 Linux 服务器上。员工只需在能访问服务器的网络中打开浏览器；服务器运行不依赖 Windows 上的程序、模型、共享文件夹或代理。

### 1. 确认部署方式

已有迁移部署使用 `/opt/rag/compose.yaml`，应用源码、数据、BGE 和 Ollama 模型分别保存在 `/opt/rag/source`、`data`、`models` 和 `ollama` 下。已经完成迁移的服务器直接从第 3 步启动，不要重新克隆项目或导入旧数据。

首次部署新服务器时，按[离线包制作](docs/OFFLINE_PACKAGE.md)和 [Ubuntu 部署指南](docs/UBUNTU.md)准备材料。该指南使用的文件名是 `/opt/rag/docker-compose.yml`，与迁移包不同，后面的命令需选择对应文件。

仓库原始 `docker-compose.yml` 只包含 RAG 服务，不能直接代替包含 RAG 和 Ollama 的企业部署配置。`setup_linux.sh` 用于宿主机 Python + Ollama 的联网本机验收，不是下面的企业容器启动入口。

### 2. 首次准备服务器

由管理员完成以下操作，具体命令见 [Ubuntu 部署指南](docs/UBUNTU.md)：

1. 安装 Docker Engine、Compose 插件，以及指南需要的 Nginx、Python 3 等运维工具。应用的 Python 依赖与 Ollama 由镜像提供。
2. 校验并导入应用镜像、Ollama 镜像、完整 BGE 和生成模型；已有数据先备份，再按迁移流程处理。
3. 保存部署 Compose 文件和 `.env`。模型接口指向容器内的 `http://ollama:11434/v1`，模型名称必须与已导入模型一致。当前迁移包使用 `qwen3:1.7b`；离线指南中的 `qwen3:4b` 是另一种交付示例。
4. 新建实例配置随机会话密钥和独立的 `root` 初始密码；迁移实例沿用原账号密码。将 `.env` 权限设为 `600`，表示只有文件所有者可读写。
5. 配置服务器访问地址。公司正式入口按指南启用内网 HTTPS；`RAG_PUBLIC_ORIGIN` 设置为实际网址，`RAG_COOKIE_SECURE=true`。

### 3. 启动与检查

以下命令在 **Linux 服务器终端**执行。先选择实际部署文件；按离线指南新装时，把第一行改为 `RAG_COMPOSE=/opt/rag/docker-compose.yml`。

```bash
RAG_COMPOSE=/opt/rag/compose.yaml
sudo systemctl enable --now docker
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" config --quiet
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" up -d --pull never --no-build
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" ps
```

这些命令以已完成镜像、模型和配置准备为前提。缺少镜像时会报错；不会自动重新下载或重建。服务在后台运行，终端返回提示符后可以关闭窗口，无需再运行 `app/main.py`。

### 4. 打开网页并确认成功

1. `ps` 显示 `rag`、`ollama` 都在运行，等待 `rag` 显示 `healthy`。
2. 在浏览器中打开实际部署网址。当前 VMware 迁移测试使用 [http://192.168.136.128:8088](http://192.168.136.128:8088)，仅适用于虚拟机仍使用该 IP 和端口映射的情况。公司服务器使用 IT 提供的内部 HTTPS 网址。
3. 新建实例使用配置的 `root` 初始密码登录；迁移实例使用原来的账号密码。
4. 确认原有资料可见，或上传一份测试资料；选择设备或资料范围，完成一次问答并看到答案和引用。

普通用户电脑不需要安装 Python、Docker、BGE 或 Ollama。VMware 测试环境需要宿主机和虚拟机开机；独立公司 Linux 服务器只需服务器自身运行。后台容器启动成功不代表登录、完整问答或 HTTPS 已通过验收。

### 5. 后续启动、停止与更新

以后启动仍执行第 3 步。Docker 开机启动与 `restart: unless-stopped` 配合可恢复未被手动停止的容器；主动停止后，需再次执行 `up -d`。

在同一终端中停止服务：

```bash
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" stop
```

重新打开终端后，先按第 3 步设置 `RAG_COMPOSE`。普通停止保留数据；不要把 `down -v` 或删除数据目录作为日常停止方式。

版本更新按[运维指南](docs/OPERATIONS.md)先备份并核验恢复，再导入新镜像、核对配置并启动验收。仅执行 `git pull` 不会更新正在运行的容器；日常启动也不需要重复执行迁移脚本。

### 6. 查看日志与运行状态

在已设置 `RAG_COMPOSE` 的终端执行：

```bash
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" logs --tail 100 rag ollama
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" exec ollama ollama list
sudo docker compose --project-directory /opt/rag -f "$RAG_COMPOSE" exec ollama ollama ps
```

无 GPU 的服务器可以使用 CPU 生成答案，首次加载模型也需要时间。页面一直等待时，结合日志检查模型加载、生成、排队及接口错误，不能仅凭等待时间判断检索慢。现有 BGE 编码固定使用 CPU；更换模型或调整参数后的效果须通过真实问题验证，见[检索质量验证](docs/RETRIEVAL_VALIDATION.md)。

公司正式入口按 [Ubuntu 指南](docs/UBUNTU.md)通过 Nginx 提供内网 HTTPS，只允许批准的员工网段访问；8088 保留在服务器回环地址，11434 不向员工设备开放。由 `root` 创建员工个人账号、授予角色并停用离职账号。详见[安全要求](docs/SECURITY.md)。

| 管理员授予的角色 | 当前权限 |
| --- | --- |
| 普通用户 `user`（调试/开发/员工） | 按所选设备问答、看引用片段；不能打开完整原文或管理文档 |
| 文档管理员 `kb_admin` | 上述权限，加完整原文查看、上传、重新处理和删除文档 |
| 系统管理员 `root` | 上述全部，加账号、角色、系统设置和审计管理 |

设备选择依据文件名缩小检索范围，未匹配的资料需手动选择；它**不是访问授权**。当前所有获授权用户仍可问答共享文档库，未实现按部门/个人隔离。普通用户不能直接下载完整文件，但短文档可能完整落在一个引用片段中，多次问答也可能逐步获知内容。若业务要求不同人员不能获知同一资料，必须先实现文档级权限，不可直接作为满足该要求的正式系统使用。

模型下载后若要求运行时禁止公网，需由系统和网络出口策略实际阻断；`.env` 中的离线开关不等于防火墙。[第一次使用](docs/GETTING_STARTED.md) · [故障排查](docs/TROUBLESHOOTING.md)

## 启动安全校验

程序在启动时会检查关键配置，**不合格直接拒绝启动**（而不是只打一条警告）：

- `RAG_SECRET_KEY` 是否已设置为随机值（缺失会让会话密钥退化为代码内公开的开发密钥）；
- `DEEPSEEK_BASE_URL` 的主机是否属于内网（私有网段、回环、单标签主机名如 `ollama`、内网后缀，或显式列入 `RAG_LLM_TRUSTED_HOSTS`）；指向公网的地址会被拒绝；
- `RAG_PUBLIC_ORIGIN` 与实际传输配置是否自相矛盾（例如 HTTPS 来源配 `RAG_COOKIE_SECURE=false`）。

被拒绝时错误信息会指出缺哪一项以及如何修复。仅在确认无风险的本机调试场景，可用 `RAG_ALLOW_INSECURE_START=1` 临时跳过全部校验，此时启动横幅会显著提示 **INSECURE MODE**。

仓库自带的 `docker-compose.yml` 只把 8088 发布到回环地址 `127.0.0.1:8088`；对外访问必须经由宿主机反向代理，见 [Ubuntu 指南](docs/UBUNTU.md)。
