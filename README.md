# Windows 个人知识库

在 Windows 主机上保存文档、检索证据并通过本机 Ollama 回答问题。手机可通过同一 Tailscale 私有网络访问，无需开放公网端口。

默认环境：Windows 10/11、Python 3.12、Ollama `qwen3:4b-instruct`、BGE-small-zh-v1.5（512 维，CPU 嵌入）。这是源码仓库，不包含私人文档、账号密码、数据库和模型权重。

## 安装与启动

先安装 Git for Windows、Python 3.12 和 [Ollama](https://ollama.com/download/windows)，并启动 Ollama。使用 PowerShell：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

安装脚本创建 `.venv`、下载依赖和模型、生成 `.env` 与随机 root 初始密码，然后启动服务。首次安装需要联网及足够磁盘空间。默认地址为 `http://127.0.0.1:8088`；保存脚本显示的初始密码。重复安装保留已有 `.env` 和数据，不强行切换已配置的模型。

以后启动：

```powershell
.\start_windows.cmd
```

详细安装、后台任务和手机连接见 [Windows 指南](docs/WINDOWS.md)。健康探针成功只表示依赖就绪，不代表所有问题都回答正确。

## 当前功能

- PDF 文本、DOCX、XLSX、TXT、Markdown 入库；扫描件需要另行 OCR。
- 关键词精确匹配与向量语义混合检索，保留文档范围和账号权限约束。
- 回答展示引用来源；资料不足时说明缺失，不以模型常识补全。
- 管理页面、用户和文档权限、会话记录、反馈及审计。

已有文档不会因拉取代码而自动重新解析。需要采用新解析或切块策略的旧文档，应先备份，再明确选择重处理；索引重建和生产部署是独立操作。仓库更新也不会自动修改另一目录中运行的服务。

## 更新与备份

先停止本目录服务并备份整个 `data` 和 `.env`，再执行：

```powershell
git pull --ff-only
.\setup_windows.cmd
```

自有源码改动应先保存；不要用强制重置覆盖。仓库不携带其他主机的数据，因此 clone 后需要安装依赖、配置账号并导入自己的文档。备份恢复见 [运维说明](docs/OPERATIONS.md)。

## 手机访问与安全

电脑和手机加入同一 Tailscale 网络，使用私有 HTTPS Serve 地址。主机需要开机、联网且不休眠，手机与电脑的物理距离不影响私网访问。不启用公网 Funnel，不将本服务直接暴露至互联网。使用 HTTPS 时配置正确的 `RAG_PUBLIC_ORIGIN` 和安全 Cookie，参见 [安全说明](docs/SECURITY.md)。

## 开发验证

安装 Python 3.12 和 Node.js 22，在独立测试环境安装 `requirements-smoke.txt`，执行：

```powershell
python -m pip install -r requirements-smoke.txt
python tests/run_checks.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/smoke_runner.ps1 -Python (Get-Command python).Source
```

测试使用合成资料、mock 模型、临时数据目录和非生产端口，不需要私人文档或模型下载。真实模型与真实文档的质量评测另行执行，不能以 mock 测试通过率代替。

通用词法、重排与多轮辅助检索的实验开关默认关闭，配置、分层评测和回滚步骤见 [通用检索实验](docs/GENERIC_RETRIEVAL.md)。

其他说明：[使用入门](docs/GETTING_STARTED.md) · [模型配置](docs/MODELS.md) · [故障排查](docs/TROUBLESHOOTING.md) · [检索验证](docs/RETRIEVAL_VALIDATION.md) · [评测工具](eval/README.md) · [开发进度](ROADMAP.md) · [English](README.en.md)
