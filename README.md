# Windows 个人知识库

把设备手册、表格和图文资料保存在本机，按文档范围检索证据，通过 Ollama 生成带引用的回答，并回到原文核对。

**当前进度：2026-11-02。** 已实现文档入库、图文说明、OCR 核对、设备范围选择、对话分页、反馈审核及独立用户与权限页面。正式前端使用 FastAPI/Jinja 模板和原生 JavaScript；Vue 和其他视觉预览未作为正式版本发布。独立业务验收目前按用户要求跳过，工程测试不能代替真实答案正确率。详见 [开发进度](ROADMAP.md)。

源码仓库不包含私人文档、账号数据、数据库、模型权重或本机预览副本。[English](README.en.md)

## 界面预览

以下截图来自独立演示环境，使用虚构账号、示例资料和模拟回答，不含本地用户数据、真实文档或图文内容。点击图片可查看大图；截图仅展示功能与交互，不代表真实业务答案的正确率。

| 问答工作台：回答、引用与历史 | 资料管理：设备类型与文档操作 |
| --- | --- |
| [![问答工作台](docs/screenshots/chat.jpg)](docs/screenshots/chat.jpg) | [![资料管理](docs/screenshots/documents.jpg)](docs/screenshots/documents.jpg) |
| **原文检索：关键词与命中片段** | **用户与权限：账号、角色与用户组** |
| [![原文检索](docs/screenshots/source-search.jpg)](docs/screenshots/source-search.jpg) | [![用户与权限](docs/screenshots/users.jpg)](docs/screenshots/users.jpg) |

[截图说明](docs/screenshots/README.md)

## 安装与启动

默认环境：Windows 10/11、Python 3.12、Ollama `qwen3:4b-instruct`、BGE-small-zh-v1.5（512 维，CPU 嵌入）。先安装 Git for Windows、Python 3.12 和 [Ollama](https://ollama.com/download/windows)，并启动 Ollama。

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

脚本创建虚拟环境、安装依赖、下载模型并生成配置和随机 root 初始密码。首次安装需要联网；保存脚本显示的初始密码。重复安装保留已有配置与数据，不强行更换已配置模型。访问 `http://127.0.0.1:8088`。

以后启动：

```powershell
.\start_windows.cmd
```

后台启动、手机私网访问和配置说明见 [Windows 指南](docs/WINDOWS.md)。

## 功能入口

| 入口 | 当前能力 |
| --- | --- |
| 问答工作台 | 全库或选中文档检索、设备快捷选择、流式回答、引用与候选来源、反馈和补充问题；历史对话每页 25 条，按页查看 |
| 资料管理 | 文档列表、设备类型、版本与适用范围、访问授权；文件和图文上传、OCR 核对、正文检查、重建索引、删除；root 可查看概览、反馈和审计 |
| 查资料 | 按关键词查索引原文，显示文件和位置；文档体检检查片段、页码和处理状态，不生成回答 |
| 用户与权限 | root 管理用户、角色、启停、密码与用户组；普通用户只能访问获授权资料 |

- 支持 PDF、DOCX、XLSX、TXT、Markdown。旧 DOC 解析需额外安装 antiword；PNG/JPEG 可填写人工说明入库，扫描 PDF 和无说明图片需核对 OCR 内容。
- 图片 AI 分析用于辅助填写说明，需配置可用视觉模型；结果经人工核对后使用，不自动成为可信答案。
- 混合检索结合关键词和向量，受文档权限、启用版本及所选范围约束。设备快捷选择依据设备标注，未标注资料不会自动归到某型号。
- “有帮助”反馈无需审核；问题反馈支持核对结果、处理记录、正确来源及可选复测信息。删除反馈不删除原问答。
- 管理页二级菜单与 URL 联动，支持直接打开、刷新恢复、浏览器前进/后退。

已移除的入口：网页导入、日志导入、反馈 CSV/回归候选导出、MCP 服务。旧来源兼容读取代码仍可能保留，不表示提供这些导入入口。没有实现相同问题自动复用历史答案的缓存。

## 数据与更新

更新前停服并备份完整 `data` 和 `.env`，保存源码版本，再执行 `git pull --ff-only` 和 `setup_windows.cmd`。本地有源码改动时先保存并解决差异，不强制覆盖。详见 [运维说明](docs/OPERATIONS.md)。

代码更新不会自动重新解析旧文档。解析策略变更时按需重处理；嵌入模型变更需要重建向量。上传、删除和重建索引应分别核验，不将探针成功视为资料正确。

默认仅本机访问；手机访问使用 Tailscale 私有网络的 HTTPS Serve。不要开放公网 Funnel。HTTPS 配置、权限和单 worker 限制见 [安全说明](docs/SECURITY.md)。

## 开发验证

使用 Python 3.12 和 Node.js 24，在独立环境运行：

```powershell
python -m pip install -r requirements-smoke.txt
python tests/run_checks.py
npm ci --prefix tests/ui
npm test --prefix tests/ui
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/smoke_runner.ps1 -Python (Get-Command python).Source
```

回归检查使用合成数据；冒烟脚本启动 mock 模型和应用，使用临时数据目录、8090/8099 测试端口，并检查重启后的持久化。需保证这些端口空闲。DOM 测试不能替代实际浏览器视觉检查，mock 问答不能证明真实模型正确率。可选 OCR 引擎验证缺少依赖时会明确跳过。

真实问题应另建经人工核对的评测集，在隔离资料副本执行，见 [业务评测流程](eval/BUSINESS_EVAL_WORKFLOW.md)。通用词法、重排、多轮辅助等实验开关不是默认启用功能，见 [通用检索实验](docs/GENERIC_RETRIEVAL.md)。

更多说明：[使用入门](docs/GETTING_STARTED.md) · [文档索引](docs/README.md) · [反馈审核](docs/FEEDBACK_REVIEW.md) · [模型配置](docs/MODELS.md) · [故障排查](docs/TROUBLESHOOTING.md) · [评测工具](eval/README.md)
