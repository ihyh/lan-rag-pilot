# 个人知识库

把设备手册、表格和图片整理成可搜索、可提问的本地知识库。回答附带资料引用，方便回到原文核对。

适用于 Windows 10/11，支持个人使用和多人协作。[English](README.en.md)

## 界面预览

以下为虚构资料与模拟回答的演示截图，点击可放大。

| 问答工作台：回答、引用与历史 | 资料管理：设备类型与文档操作 |
| --- | --- |
| [![问答工作台](docs/screenshots/chat.jpg)](docs/screenshots/chat.jpg) | [![资料管理](docs/screenshots/documents.jpg)](docs/screenshots/documents.jpg) |
| **原文检索：关键词与命中片段** | **用户与权限：账号、角色与用户组** |
| [![原文检索](docs/screenshots/source-search.jpg)](docs/screenshots/source-search.jpg) | [![用户与权限](docs/screenshots/users.jpg)](docs/screenshots/users.jpg) |

## 快速开始

先安装 Git、Python 3.12 和 [Ollama](https://ollama.com/download/windows)，并启动 Ollama。然后在 PowerShell 中运行：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag
cd C:\rag
.\setup_windows.cmd
```

首次安装需要联网下载依赖和模型。请保存安装时显示的 `root` 初始密码。

打开 **http://127.0.0.1:8088**，登录后上传资料，等待处理完成即可提问。

以后启动，在安装目录双击 `start_windows.cmd` 即可。

## 主要功能

| 功能 | 可以做什么 |
| --- | --- |
| 问答工作台 | 根据资料提问、查看引用、翻阅历史对话 |
| 资料管理 | 上传文件和图片，按设备分类，核对识别文字 |
| 查资料 | 搜索原文关键词，定位文件与相关片段 |
| 用户与权限 | 管理账号，设置不同用户的资料访问范围 |

支持 PDF、Word（DOCX）、Excel（XLSX）、TXT、Markdown 和 PNG/JPEG 图片。扫描件与图片识别结果需人工核对。

## 数据说明

资料和对话默认保存在本机。仓库不包含个人账号、文档或数据库。更新前请备份 `data` 文件夹和 `.env` 配置。

## 使用帮助

[安装与手机访问](docs/WINDOWS.md) · [首次使用](docs/GETTING_STARTED.md) · [模型设置](docs/MODELS.md) · [备份与更新](docs/OPERATIONS.md) · [常见问题](docs/TROUBLESHOOTING.md) · [全部文档](docs/README.md)
