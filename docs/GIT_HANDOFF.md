# Git / GitHub 交接

更新时间：2026-09-04

## 当前状态

- 本地仓库已初始化，默认分支为 `main`。
- 首次提交：`86abf10 chore: initial RAG pilot`。
- 当前尚未配置 GitHub remote，也未推送到任何远程仓库。
- 建议创建空的 **Private** 仓库，名称可用 `lan-rag-pilot`。

## 提交边界

已提交源码、部署模板、文档、测试、CI 和 `.env.example`。

以下内容由 `.gitignore` 排除，禁止提交：`.env`、`.env.*`（`.env.example` 除外）、`data/`、`models/`、`backups/`、`.venv/`。

## 配置远程并推送

在 GitHub 创建空的私有仓库后，在项目根目录执行：

```powershell
git remote add origin git@github.com:组织或用户名/lan-rag-pilot.git
git push -u origin main
```

如果使用 HTTPS：

```powershell
git remote add origin https://github.com/组织或用户名/lan-rag-pilot.git
git push -u origin main
```

HTTPS 登录使用 Personal Access Token 或 Git Credential Manager，不使用 GitHub 账户密码。

## 远程仓库建议

- `main` 设置为默认分支并启用保护：Pull Request、CI 通过、禁止 force push。
- 先只启用 CI，不自动部署 Ubuntu；部署仍按 `DEPLOYMENT_HANDOFF.md` 手动执行。
- 生产 `.env`、SQLite 数据库、上传文档和模型缓存放在 Ubuntu 独立目录或备份存储，不放入 GitHub。
