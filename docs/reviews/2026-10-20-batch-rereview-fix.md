# R4 并发刷新修正（第二轮复审后）

日期：2026-10-20
分支：`feat/manual-document-scope-20261020`
上一复审 HEAD（Codex 终检）：`6de70102be476b0a136342cf4389e7f7636b7037`
本轮修复提交：见本文件所在提交（`fix(r4)` 开头）；未改动 R1–R3、R5–R7 与网页应用代码。
归档的 Codex 复审原文：`docs/reviews/2026-10-20-batch-codex-rereview.md`

## 复审结论与剩余问题

R5/R6/R7 通过，R1–R3 未见回归；唯一剩余问题：**R4 / P2 不能把加载后读取的新指纹绑定到旧索引**。
原实现先 `vector_index.reload(db)`、再单独 `_index_fingerprint(db)`；两者不在同一 SQLite 读
快照。若另一进程恰好在中间提交新增文档/切片，新指纹会与旧索引一起保存，之后指纹恒等，
刷新被永久跳过（Codex 用确定性调度复现：连续 3 次限定检索为空、`ask_question` no_evidence）。

## 修复

`scripts/mcp_server.py` 新增 `KnowledgeBase._reload_index_consistent()`，首次加载与后续刷新共用：

1. 在**同一个显式 SQLite 读事务**（`BEGIN ... ROLLBACK`）中执行 `vector_index.reload(db)`
   并读取 `_index_fingerprint(db)`，两者来自同一读快照（WAL 下读快照一致）；
2. 事务结束后重新读取数据库当前指纹：若与已加载快照一致 → 保存为已加载指纹；不一致说明
   期间有外部提交 → 重试（最多 3 次）；
3. 连续遇到并发提交时**保存与当前索引一致的 loaded 指纹**（不保存 current），
   使下一次工具调用继续检测并刷新，绝不把尚未加载的更新标记为已加载；
4. `ensure_fresh_index()` 先做一次当前指纹比较，不一致才进入一致的 reload；`ensure_ready()`
   首次加载也走同一函数。重载嵌入模型不在本路径内。

## 新增回归（`tests/mcp_server_check.py`，6 项）

用确定性注入模拟"原 reload 返回后、指纹读取前，另一连接提交新 ready 文档"：

1. **首次加载边界**：注入发生在 `KnowledgeBase()` 首次加载期间 → 索引与保存指纹来自同一快照
   （两者相等），且该文档在后续 `retrieve_sources` 中可见；
2. **后续刷新边界**：注入发生在 `search_documents` 触发的刷新期间 → 连续 3 次限定检索都能
   看到新文档（对应 Codex 的"连续 3 次空"复现）；
3. **`ask_question` 入口**：先做一次普通变更触发刷新，再在刷新期间注入新文档 →
   问答来源立即包含该文档；
4. 写入结束后后续 `search_documents` 自行恢复；
5. 刷新完成后长期指纹与数据库一致。

另外用临时脚本（不提交仓库）验证回归的敏感性：把 `_reload_index_consistent` 临时换回旧实现
后，同一注入用例复现 **0 条来源**；换回修复实现后得到 **1 条来源**。证明新回归确实能抓住
该并发缺陷，而不是空跑。

## 回归结果（修复后）

| 验证 | 结果 |
| --- | --- |
| `python tests/mcp_server_check.py` | **43/43 通过**（37 项原有 + 6 项 R4 并发/一致性） |
| `python tests/run_checks.py`（全量离线） | **38/38 通过**（MCP 检查内含 43 项） |
| `tests/injections/check_anchors.py` | 37 锚点通过（含在 run_checks） |
| 端到端 smoke | 本轮未改 `app/` 与 MCP 之外代码；上一轮 218 API + 重启后 22 项结果仍适用，未重复声明为新结果 |
| 旧证据 | `data/eval/reports/batch-review-20261020/`、`batch-rereview-20261020/`、`.verify/ocr-live-evidence*` 均未覆盖 |

## 未改 / 未测

- 未改 OCR（R5–R7 已通过）、未改前端/网页检索（R1–R3 已通过）、未加第二套索引。
- 未重跑真实浏览器、实体手机、真实 DeepSeek MCP 客户端；OCR 导入端到端准确率仍未验收。
- 外部脚本"原地改写同一 chunk 行"仍不会被指纹识别（应用本身不产生该变更，已写入文档）。
- 未合并、未部署；修复提交推送后请做第三轮复审。
