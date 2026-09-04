# RAG 试点安全与可维护性审计

更新时间：2026-09-04

## 已核对并已落地

- 用户、审计、反馈导出、概览和系统设置仅允许 root；普通 user 的管理请求返回 403。
- `kb_admin` 只能管理所属部门的知识库和文档；跨部门共享文档不允许其删除、重建或重新分配。
- 问答详情只允许本人或 root 查看；不存在/他人记录统一返回 404。
- 密码使用 Argon2id；会话 Cookie 为 HttpOnly、SameSite=Lax；数据库只保存会话令牌 HMAC 哈希。
- API 写请求有 Origin/Referer 同源校验；API 响应设置 no-store，避免问答、审计和反馈被浏览器缓存。
- 上传限制大小、扩展名、MIME、魔数、实际解析和 SHA-256 去重；归档恢复脚本拒绝符号链接和越界路径。
- user 查询会过滤到其部门下知识库的 ready 文档；root 不受该范围限制。
- 引用原文接口复用同一授权范围；无权限与不存在统一返回 404，成功访问写入 `document_open` 审计并禁止浏览器缓存。
- 向量索引遇到空/损坏 BLOB 会跳过，不因单条脏数据阻断启动；真实嵌入支持低相似度拒答阈值。
- `/api/health` 是存活探针，`/api/ready` 是模型就绪探针；Compose healthcheck 使用后者。

## 本轮本机复核证据

- 在隔离临时目录、mock 嵌入和 mock DeepSeek 下运行 `tests/smoke_runner.ps1`：127 项通过、0 项失败；包含三角色越权、跨部门共享保护、无部门权限拒答、反馈、CSV、5 用户并发、LLM 异常和重启持久化。
- `.venv` 已补齐 smoke 所需最小依赖；`pip check`、Python/JavaScript 语法检查、Shell 语法检查、`docker compose config --quiet` 和评测脚本检查均通过。
- 管理页的部门/知识库分配提示现在同时展示 ID 与名称，仍保留按 ID 提交的简单流程。
- GitHub Actions CI 已加入 push/PR 门禁：使用轻量 smoke 依赖，执行 Python/JavaScript/Shell/Compose 检查和 Linux/Docker 等价 smoke，不读取生产密钥或数据。

## 上线前仍必须完成

1. Ubuntu 安装每日 cron，并将备份复制到独立磁盘或受控存储；完成一次新机器恢复演练。
2. 通过公司内网证书或反向代理启用 HTTPS，Compose 端口只绑定本机，设置 `RAG_COOKIE_SECURE=true`。
3. 更换 `root/fcd123`，配置高熵 `RAG_SECRET_KEY`，每个人使用独立账号。
4. 防火墙只允许公司网段访问 443；不要把 8088 暴露到公网。
5. 用真实文档人工填写至少 30 条 `data/eval/questions.jsonl`；先记录固定 BGE + `qwen3:1.7b` 基线，不同时调整检索参数。
6. 在另一台公司电脑和手机 Wi-Fi/VPN 上验证登录、问答、引用、反馈和 root 管理页。

## 有意保留的试点边界

- 单进程、SQLite、内存向量索引；超过约 5 万切片或出现并发瓶颈再迁移 PostgreSQL + pgvector。
- 逻辑角色为 root/kb_admin/user；为兼容旧 SQLite CHECK，`kb_admin` 在库内存为 `role='user'` 加 `is_kb_admin=1`。SSO、机器人、OCR、Excel/PPT 未实现。
- 部门权限当前是“部门→知识库→文档”；管理员在页面中按 ID 分配，后续可换成多选控件。
- 用户自己的历史问答保留可见；若公司要求“撤销部门后连历史引用也不可见”，需要先确定历史访问策略，再增加访问快照或历史脱敏，不能简单删除记录。

## 外部依赖状态

本审计只覆盖源码、配置模板和本机静态检查。Ubuntu 实机重建、cron 安装、DNS、HTTPS、Windows/公司防火墙、企业微信/飞书后台和真实评测均需按现场证据验收，未执行的项目不标记为已完成。
