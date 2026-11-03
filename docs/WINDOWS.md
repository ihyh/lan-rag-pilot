# Windows 安装与手机访问

## 安装

准备 Windows 10/11、Git for Windows、Python 3.12 和已启动的 Ollama。首次下载需要联网；模型文件大小不等于实际内存、显存占用，应给系统、模型和文档索引留出空间。

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

脚本创建 `.venv`、安装 CPU 版 PyTorch 和应用依赖、下载 BGE 嵌入模型，以及在缺少时下载 `.env` 配置的 Ollama 模型。新安装默认 `qwen3:4b-instruct`，不会覆盖已有 `.env` 的模型、密钥和数据。保存首次显示的 root 密码。后续启动使用 `start_windows.cmd`。

手动配置可复制 `.env.example` 为 `.env`，填写 `RAG_SECRET_KEY` 和 `RAG_ROOT_PASSWORD`，安装 `requirements.txt` 并通过 `scripts/install_bge.py` 安装嵌入模型。关键设置示例：

```dotenv
DEEPSEEK_API_KEY=ollama
DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1
DEEPSEEK_MODEL=qwen3:4b-instruct
RAG_EMBED_MODEL=models/bge-small-zh-v1.5
RAG_EMBED_BACKEND=st
RAG_EMBED_DEVICE=cpu
RAG_HOST=127.0.0.1
RAG_PORT=8088
RAG_PUBLIC_ORIGIN=http://127.0.0.1:8088
RAG_COOKIE_SECURE=0
```

脚本读取 `.env` 时采用一行一个 `KEY=value`，不要添加外层引号或行尾注释。密钥和密码保持私有。CPU 安装不能直接把设备改成 CUDA；要使用 GPU，先安装匹配驱动的 CUDA 版 PyTorch，再设置设备并核对 `/api/ready` 的 `embed_device`。

## 启动与后台运行

`start_windows.cmd` 前台运行；关闭对应窗口会停止本次服务。Ollama 应另行保持运行。访问 `/api/health` 检查进程，访问 `/api/ready` 检查嵌入与生成模型；200 不代表答案内容正确。

需要后台运行时可在 Windows 任务计划程序创建单实例任务。程序选择实际安装的 Python 3.12 `python.exe`（不是不存在的示例路径），参数为：

```text
-I -S -u C:\rag\scripts\run_personal_service.py
```

起始目录填写 `C:\rag`。该入口加载本目录 `.env` 和 `.venv`，日志写入 `logs/service-*.out.log` 与 `logs/service-*.err.log`。先在终端验证入口，再配置开机触发；不要同时运行前台和后台两个实例。服务仅支持单 worker。Ollama 不由此入口自动启动。

## 手机通过 Tailscale 访问

电脑和手机登录同一 Tailscale 网络，并在手机打开连接。在电脑终端检查并配置私有转发：

```powershell
tailscale status
tailscale serve --bg http://127.0.0.1:8088
tailscale serve status
```

若提示启用 HTTPS，按 Tailscale 提供的页面完成设置。使用状态中实际显示的 `https://设备名.网络名.ts.net`，不要照抄其他机器的地址。手机连接后，浏览器访问该地址。仅用 Serve，不启用公网 Funnel。

把 `.env` 的 `RAG_PUBLIC_ORIGIN` 改为实际 HTTPS 地址，设置 `RAG_COOKIE_SECURE=1`，重启应用后从 HTTPS 地址登录。不要将这个 Cookie 配置用于纯 HTTP 登录。

距离不影响连接；主机必须联网、开机且不休眠。Windows“设置 → 系统 → 电源”中将接通电源时的睡眠设为“从不”；显示器可以熄屏。笔记本还需检查合盖动作与散热。

## 文档导入

支持文本 PDF、DOCX、XLSX、TXT、Markdown；扫描 PDF 需先 OCR，旧 `.doc` 需要可用的 antiword 或另存 DOCX。等待管理页显示处理完成再提问。旧文档采用新解析或切块时需显式重处理，事前备份；不要以拉取代码代替重建和质量验证。

更多：[模型](MODELS.md) · [排查](TROUBLESHOOTING.md) · [备份](OPERATIONS.md)
