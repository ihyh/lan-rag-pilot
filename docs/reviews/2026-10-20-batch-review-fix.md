# 批量复核修正（R4–R7）提交说明

日期：2026-10-20
分支：`feat/manual-document-scope-20261020`
上一复核 HEAD（Codex 复核基线）：`0d44feb`
本轮修复提交：见本文件所在提交（`fix(review)` 开头）；不含 R1–R3 回退、不含新功能。
归档的 Codex 复核原文：`docs/reviews/2026-10-20-batch-codex-review.md`

R1/R2/R3 保持不变（Codex 已确认通过）；本轮只处理新发现的 R4、R5、R6、R7，优先修 R4。

## R4 / P1：长驻 MCP 索引永久停留在首次调用时

**处理**（`scripts/mcp_server.py`）：

- 新增资料指纹 `_index_fingerprint`：documents/chunks 的行数、最大 ID、ready/parsing 数量、
  documents.updated_at。指纹覆盖新增、删除、状态变化与同文档重建（chunks 重建会改变行数/ID）。
- 新增 `KnowledgeBase.ensure_fresh_index()`：`search_documents` / `ask_question` 在检索前比较
  指纹；变化时调用现有 `vector_index.reload` 重载内存索引，**不重载嵌入模型**；
  `list_documents` / `get_document_text` 仍直接读库，天然最新。
- 全库范围不再返回 `None`：`_resolve_scope` 始终返回**当前 ready 文档集合**作为 `scope`，
  旧索引里已删除/非就绪文档的切片会被过滤；显式 `document_ids` 仍按最新 ready 集合校验，
  已改为非就绪的文档会明确报错。
- `ask_question` 与 `search_documents` 共用同一条刷新/范围路径。
- 已知局限（写入文档）：应用不会原地改写同一 chunk 行；外部脚本原地改内容不会被指纹识别，
  需重启进程。

**新增回归**（`tests/mcp_server_check.py`，同一 MCP stdio 会话、不重启）：

1. 另一连接新增文档 3 + 有效 mock 向量 → `search_documents(document_ids=[3])` 立即命中；
2. 文档 2 改为 failed → 列表排除 2、全库检索结果只含 `{1,3}`、限定 `[2]` 明确报错；
3. 文档 1 重新索引（chunks 重建）→ 限定检索返回新切片内容（`重新索引后`）；
4. `ask_question(document_ids=[3])` 在新增后立即可用；
5. 与网页 `/api/query` 在**变更后**同范围对照来源身份一致；
6. 全库来源仍保留无命中/范围过滤控制。

结果：MCP 检查 **37/37 通过**（原 30 项 + 7 项 R4）。

## R5 / P2：接受多页 TIFF，却静默只取第一页

**处理**（`scripts/ocr_preprocess.py`）：

- `ImageFileRenderer` 读取并暴露 `page_count`（Pillow `n_frames`），`render(index)` 按帧 `seek`；
- 图片分支改为**逐帧处理**，与 PDF 一致支持页码映射、`--pages`、`--max-pages`；
  单帧图片行为不变；某一帧解码失败只把该页标记 `render_failed`，不再整文件“成功”；
- 帧数/页码写入 `source.pages` 与 `pages[].page`，文本导出仍用 `\f` 对应。

**新增回归**：

- 离线（假渲染器/假引擎）：两帧 TIFF → 页码 1/2、两页都 OCR、两帧文字都保留、
  文本导出 1 个分页符；`--pages 2` 只处理第二帧；`--max-pages 1` 第二帧 skipped；
  第二帧解码失败 → 第 2 页 failed + `render_failed`、退出码 1。
- 真实引擎（合成两帧 TIFF，第 1/2 帧不同正文）：帧数与页码完整、两帧都 OCR、
  两帧文字与第二帧数字/单位（0.6 MPa）均保留、导出页码映射正确。

## R6 / P2：低置信度的关键行被页面均分掩盖

**处理**：只要存在低于 `--line-score` 的行，页面新增 `low_confidence_line` warning 并置
`status=suspect`；保留原 `uncertain_lines`（原文/真实分数）。文件整体为 `partial`；
可疑页不改变退出码约定（不是处理失败）。页面均分低仍另有 `low_confidence`。

**新增回归**（离线）：9 行 0.99 + 1 行 0.55（含数字/单位）→ 页面均分 > 0.9、
`status=suspect`、`warnings` 含 `low_confidence_line` 且不含 `low_confidence`、
`uncertain_lines` 保留该行、文件 `partial`、退出码 0；全高分页仍 `ok`；无置信度引擎仍不伪造。

## R7 / P2：未开启覆盖也会覆盖旧运行清单

**处理**：清单与输出采用一致的覆盖语义：

- 首次运行仍写 `ocr_run.json`；
- 目录已有清单且未 `--overwrite` 时，本次写唯一的 `ocr_run-<时间戳>[-N].json`，
  **旧清单字节不变**；
- `--overwrite` 时才覆盖固定名清单；
- 清单内容新增 `manifest` 字段记录自身文件名，仍包含 `items/exit_code`。

**新增回归**（离线）：首次成功后记录三个输出/清单的 SHA-256；同输入无覆盖重跑 → 退出码 1、
旧输出与旧 `ocr_run.json` 哈希完全不变、新清单记录 `output_exists` 失败；同目录处理新一批
输入成功且旧清单仍不变；`--overwrite` 重跑后固定清单记录成功。

## 回归命令与结果（修复后）

```powershell
python tests/ocr_preprocess_check.py        # 离线 OCR：15/15 通过
python tests/ocr_preprocess_live_check.py   # 真实引擎合成样例：38/38 通过
python tests/mcp_server_check.py            # MCP 协议：37/37 通过
python tests/run_checks.py                  # 全量离线：38/38 通过
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/smoke_runner.ps1 -Python <venv python>
                                            # 端到端：218 API + 重启后 22，0 失败
```

`tests/injections/check_anchors.py`（37 锚点）与 latency 注入组仍通过；`run_injections --group probe`
的 `trust_env` 失败为既有环境问题（已在基线快照复现），本轮未改动。

## 未改 / 未测

- 未改 R1–R3 相关前端/范围代码；网页 `retrieve_sources` 与 `/api/query` 本轮无改动。
- 未重跑真实浏览器脚本（本轮无前端改动）；实体手机/Tailscale/Safari/软键盘仍未验证。
- MCP 真实 DeepSeek 客户端、Streamable HTTP/远程/鉴权仍未做；索引指纹对“原地改同一 chunk 行”
  不敏感（应用不产生这种变更，已写入文档）。
- OCR 导入集成、旧文档重处理/索引迁移仍未做；多帧仅支持 TIFF。
- 未合并、未部署；修复提交推送后 Codex 可做第二轮复审。

## 证据位置（本机，未提交仓库）

- 修正后真实 OCR 证据：`.verify/ocr-live-evidence-fix/`（两帧 TIFF、结果 JSON、摘要）。
- 旧证据目录未覆盖：`.verify/ocr-live-evidence/`、`data/eval/reports/manual-scope-review-20261020*/`、
  `data/eval/reports/batch-review-20261020/`。
- 全量回归与冒烟日志：`.tmp-tests/rag-checks-*/`、`.tmp-tests/rag-smoke-*/`。
