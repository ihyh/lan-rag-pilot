# 源码与离线发布交付

[返回首页](../README.md) · [制包](OFFLINE_PACKAGE.md)

仓库：[ihyh/lan-rag-pilot](https://github.com/ihyh/lan-rag-pilot)。本文不声明 GitHub 当前分支保护、PR 状态或线上部署版本；以管理员当次核对记录为准。

## 准备机取得源码

仅在获准联网的准备机操作，运行机不能 git pull。先按审核流程选定完整 commit，而不是无条件追随 main。

准备机 PowerShell，在新空目录创建副本：

```powershell
git clone https://github.com/ihyh/lan-rag-pilot.git C:\rag-source
Set-Location C:\rag-source
git status --short
git log -5 --oneline
$releaseCommit = 'REPLACE_WITH_APPROVED_FULL_COMMIT'
if ($releaseCommit -like 'REPLACE*') { throw '先填写已审核的源码 commit' }
git show --no-patch --format=fuller $releaseCommit
git archive --format=zip --output=C:\rag-source.zip $releaseCommit
Get-FileHash -Algorithm SHA256 C:\rag-source.zip
```

确认导出根目录有 app、scripts、requirements.txt。git archive 只导出提交内容，不带工作区未提交改动。构建镜像、制包和文档版本必须对应同一批准提交；新文档尚未提交时，不能声称旧 commit 的 archive 已含这些文档。

若包中需加入经批准但尚未提交的补丁，单独记录 patch 及其摘要，不伪称完全等于某个 commit。

## 提交与发布边界

实际 .env、data、models、备份、凭据和私人评测不提交。规划记录 task_plan.md/findings.md/progress.md 仅本地使用。

修改后先检查 git diff，只暂存明确的任务文件，不用 git add . 夹带其他变更。确认变更范围、回归与密钥检查后，按组织流程提交、推送、PR 审查。推送或创建 PR 需要明确发布授权，本教程不会自动执行。

本地修改、GitHub 合并、离线包交付、目标服务器验收是四件不同的事。每阶段留证据；不能因为 GitHub 更新就说运行程序也已更新。

## 运行机接收

接收核验过的包，按 [运维升级](OPERATIONS.md)在独立实例演练并备份。运行机不保存 GitHub 令牌、SSH 私钥或公网 API 凭据，也不直接构建需要下载的镜像。

[历史部署记录](../DEPLOYMENT_HANDOFF.md)只用于追溯，不能照抄其中实验地址或实时状态。
