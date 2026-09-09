# Git / GitHub 交接

更新时间：2026-09-09

## 当前状态

- 仓库：[ihyh/lan-rag-pilot](https://github.com/ihyh/lan-rag-pilot)，已配置 `origin`；GitHub 核验为 **Public**，默认分支为 `main`。
- GitHub `main` 当前合并提交为 `fbac8d3`，已包含 PR #4 的全部功能；对应应用提交为 `2631f8e`。
- 当前文档整理分支：`codex/refresh-project-docs`；本次只更新正式项目文档，不重复提交已经合并的源码。
- `main` 已开启分支保护，更新必须经过 Pull Request，禁止直接推送和强制推送。
- PR #4 已于 2026-09-09 合并。当前文档分支尚未创建 PR；创建和合并仍需单独确认，只有合并后 GitHub 默认首页才会显示新版 README。

## 已完成的里程碑

| 提交 | 内容 |
|---|---|
| `24f6135` | 统一文档库，简化文档上传、筛选和引用展示 |
| `236e83e` | 多轮 RAG 对话、对话权限与删除、旧问答迁移及接口兼容 |
| `7baae92` | 接入远端 `main` 合并历史，不改变此前已验证的功能代码 |
| `43308fe` | 关闭 Qwen3 隐藏思考，新增请求参数回归并接入 CI |
| `7de2521` | 优化回答约束、去重和端到端等待时间 |
| `740c1c3` | 对话绑定所选文档范围，避免设备资料混用 |
| `9a0b80c` | 增加技术型号和参数名的精确词混合召回 |
| `27a34d4` | 提问页增加 PLM、PLUSPRO、PLUS500 设备快捷选择 |
| `2631f8e` | 支持旧 Word 97–2003 DOC，并解释“重新处理”操作 |

2026-09-09 实时核验 Ubuntu 正在运行 `2631f8e`：容器 healthy、SQLite 完整、BGE 与内网 Ollama 就绪；localhost、VM 和当前 WLAN 入口的 health/readiness 均为 HTTP 200。代码完成、GitHub 合并和线上部署仍是三个独立验收项。后续工作见 [路线图](../ROADMAP.md)。

## 提交边界

已提交源码、部署模板、文档、测试、CI 和 `.env.example`。

以下内容由 `.gitignore` 排除，禁止提交：`.env`、`.env.*`（`.env.example` 除外）、`data/`、`models/`、`backups/`、`.venv/`。不得在命令输出、提交说明、Issue 或 PR 中粘贴实际口令、私钥、API Key、公司文档及业务问答摘录。

`task_plan.md`、`findings.md`、`progress.md` 及 `.planning/` 隔离规划记录只保留在本地，不因未被忽略就允许提交。`DEPLOYMENT_HANDOFF.md` 只有在实时核验容器、数据库、模型和入口后才能更新并提交；历史部署说明不能代替当前证据。

## 后续提交流程

1. 先读项目文档和本地工作记录，检查 `git status --short --branch`、`git log -6 --oneline --decorate --graph`、`git diff` 与 `git diff origin/main...HEAD`。
2. 只暂存本轮明确允许提交的文件；检查 `git diff --cached --stat` 和 `git diff --cached --check`，不使用 `git add .` 夹带本地记录。
3. 根据变更运行静态检查、mock smoke 和专项测试；涉及部署、数据或模型时，另做受控的实时验证，不使用生产数据跑破坏性 smoke。
4. 提交后只推送到已确认的功能分支，再用 `git ls-remote` 检查远端 SHA；`ahead` 或本地提交成功不等于已经上传。
5. 得到确认后创建 `base=main`、`head=<当前功能分支>` 的 PR。当前 CI 仅在推送 `main` 或向 `main` 发起 PR 时运行，功能分支 push 本身不会触发。

在已确认当前分支为 `codex/refresh-project-docs` 且提交范围正确后：

```powershell
git push origin HEAD:refs/heads/codex/refresh-project-docs
git ls-remote origin refs/heads/codex/refresh-project-docs refs/heads/main
```

SSH 不可用时，可用仓库 HTTPS 地址与已有 Git Credential Manager 授权推送；不要读取或打印凭据，不把令牌写入远程 URL，不为绕过保护推送 `main`。

## 远程仓库建议

- 保持 `main` 默认分支和保护规则，合并前检查 PR、CI 与审查要求。
- 不自动部署 Ubuntu；获得部署确认后，结合实时状态、备份及回滚准备执行，不能直接照搬旧交接文档。
- 生产 `.env`、SQLite 数据库、上传文档和模型缓存放在 Ubuntu 独立目录或备份存储，不放入 GitHub。
