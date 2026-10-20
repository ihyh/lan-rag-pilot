# 批量交付与一次性复核说明（2026-10-20）

按用户要求：先完成开发并提交，再一次性交给 Codex 复核（不再逐轮送审）。
本文件汇总本轮复核的基线、提交、验收点、测试命令与结果、未测项与证据位置。

## 基线与提交

- 分支：`feat/manual-document-scope-20261020`
- 基线：`origin/main` `5689e78`（与接手时本地 HEAD 树一致）
- 本轮复核范围（按交接文档顺序）：

| 轮次 | 提交 | 内容 |
| --- | --- | --- |
| 第一轮：手动勾选文档 | `8a22f6f` | 显式范围语义 + 检索范围卡片（已由 Codex 首轮复核） |
| 第一轮复核修正（R1/R2/R3） | `8e05648` | 生成中锁定范围、服务端 `scope_mode` 恢复、100 文档上限 |
| 第二轮：OCR 预处理小工具 | `d6d6420` | 独立按页 OCR 工具 + 离线/真实引擎测试 |
| 第三轮：MCP 小服务 | `cebc50d` | stdio 只读 MCP 服务 + 共享检索路径抽取 |
| 文档记录 | `4983be5`、`f3b57fe`、本提交 | 审核反馈归档、各轮提交说明、本汇总 |

说明：Codex 首轮复核反馈要求“先修正 R1/R2/R3，不要混入 OCR/MCP”；修复已单独提交 `8e05648`，
OCR 与 MCP 分别在其后独立提交，互不混入。若需要拆分为独立 PR，可按提交边界拆开。

## 复核入口（建议）

```powershell
git diff 8e05648..cebc50d          # 第二轮 + 第三轮（OCR / MCP 及共享检索抽取）
git diff 5689e78..8e05648          # 第一轮功能 + R1/R2/R3 修复（含测试）
git show --stat cebc50d d6d6420    # 两轮改动清单
```

## 各轮验收与测试结果

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 全量离线回归（含第一轮 DOM、OCR、MCP） | `python tests/run_checks.py` | **38/38 通过** |
| 端到端冒烟（mock 嵌入 + mock 模型） | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/smoke_runner.ps1 -Python <venv python>` | **API 218 项 + 重启后 22 项，0 失败** |
| 第一轮：真实浏览器桌面 + 390×844 移动视口 | 本机 `.verify/run_browser_check.ps1`（headless Edge CDP，未提交） | **47/47 通过**（含 R1 生成期间锁定回归） |
| 第一轮：Codex 复现脚本复跑（修复后） | `data/eval/reports/manual-scope-review-20261020-fix-rerun/` | 前端 **5/5**、API **15/15**、安全回归 **7/7** |
| 第二轮 OCR 离线回归 | `python tests/ocr_preprocess_check.py` | **12/12 通过** |
| 第二轮 OCR 真实引擎合成样例 | `python tests/ocr_preprocess_live_check.py` | **30/30 通过**（中文/英文召回 1.000；混合 PDF 分流正确） |
| 第三轮 MCP 协议级 | `python tests/mcp_server_check.py` | **30/30 通过**（真实 MCP 客户端；与网页来源一致） |

## 未测项（本轮未覆盖，不声称已完成）

- 实体手机 + Tailscale HTTPS 实机、Safari、软键盘遮挡（第一轮用 Edge 移动视口，不是实体机）。
- 真实私有文档/真实模型下的答案准确率（所有测试均为合成资料 + mock 或合成样例；范围/引用契约不等于业务正确率）。
- OCR 接入导入界面、旧文档重新处理与索引迁移（按交接要求另立提交，需备份与历史引用兼容性方案）。
- OCR 对低质量扫描、透视畸变、复杂版面的还原（已记录旋转页行序反转、表格行列串联等实际差异）。
- MCP 在真实 DeepSeek 等客户端的接入体验、Streamable HTTP/远程访问与鉴权（本轮只做本机 stdio 最小范围）。
- 任何部署、合并、推送；本批提交均未离开本机仓库。

## 已知无关问题

`tests/injections/run_injections.py --group probe` 在本机有既有的环境相关失败（`I1 恢复
trust_env=True` 注入未变红），已在基线 `8e05648` 的干净快照上复现，与本次改动无关；
`check_anchors.py`（37 个锚点）与 latency 注入组均通过，latency 锚点已随共享检索抽取同步更新。

## 证据位置（均为本机，未提交仓库）

- 第一轮：`<workspace>/.verify/artifacts/`（截图 `desktop-selected-scope.png`、
  `mobile-selected-scope.png`、`browser-results.log`；脚本 `scope_browser_check.cjs`、运行入口）。
- R1/R2/R3 复跑：`lan-rag-pilot/data/eval/reports/manual-scope-review-20261020-fix-rerun/`
  （`frontend-results.json`、`api-results.json`、`regression-results.json`；旧失败结果
  `manual-scope-review-20261020/` 原样保留）。
- 第二轮：`<workspace>/.verify/ocr-live-evidence/`（合成图片、`.ocr.json`/`.ocr.txt`、
  `ocr-live-summary.json`）。
- 第三轮：`tests/mcp_server_check.py` 输出（临时数据库自动清理；未保存私有数据）。

## 结论边界

本批提交只声明“在已测的合成/离线范围内满足交接文档的行为与契约要求”，
不构成业务准确率证明、验收通过、合并或部署批准。
