# Windows 个人服务运维

## 备份

先停止前台应用或它对应的计划任务，确认没有另一个实例写入。备份完整 `data`（含 SQLite、上传文件和相关状态）与 `.env`；不要只复制正在写入的单个 SQLite 文件，遗漏 WAL 会导致备份不一致。

下面假设安装在 `C:\rag`，目标盘存在且有空间；改成自己的实际路径：

```powershell
$backupPath = Join-Path 'D:\rag-backups' (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $backupPath -ErrorAction Stop
Copy-Item -LiteralPath 'C:\rag\data' -Destination $backupPath -Recurse -ErrorAction Stop
Copy-Item -LiteralPath 'C:\rag\.env' -Destination $backupPath -ErrorAction Stop
git -C C:\rag rev-parse HEAD
ollama list
```

另行保存输出中的源码版本、模型名称与标识。备份含私人资料与凭据，应限制访问并妥善加密保存；不要推送到 GitHub。

## 更新

停服并备份后，在安装目录执行 `git pull --ff-only`，再运行 `setup_windows.cmd`。有本地源码修改时先保存并解决差异，不要强制覆盖。重新启动后检查 `/api/health`、`/api/ready`、登录和一条有明确证据的问答。

代码更新、生产部署、旧文档重新解析是三件事，分别记录时间、源码版本、模型和测试结果。改变嵌入模型需要重建向量；生成模型切换通常不需要重建。CEID 分块升级需要重处理相关旧文档，不会自动迁移已有切片。

## 恢复演练与回滚

在全新独立目录恢复备份，使用独立数据路径和非生产端口（例如 18088），不要覆盖运行中的服务。密钥、数据库和上传文件需要成套恢复。检查文档数量、权限、引用链接及已保存问答；确认后才安排生产切换。

重处理可能更改片段标识，保留原始问题、答案与失败证据，不能仅以数量或探针通过认定恢复成功。回滚使用记录的源码版本及对应备份，不混用新旧数据。先停服，再切换，不并行启动同一数据库的两个应用。

## 日常检查

查看后台入口 `logs`、管理页文档处理状态、模型实际加载情况与可用空间。保持主机不休眠并保持 Ollama/Tailscale 在线。安全边界见 [安全说明](SECURITY.md)，启动问题见 [故障排查](TROUBLESHOOTING.md)。
