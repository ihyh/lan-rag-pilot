# OCR → 隔离库 → 检索/引用/回答 端到端验收（首轮）

日期：2026-10-21
范围：隔离测试库与合成扫描式样本；不接生产、不改生产索引/服务、不部署。
本文件记录第一次把 OCR 恢复正文、原始 PDF、页码、检索引用和本地模型回答串起来的
端到端结果，以及仍然不能声称的结论。

## 样本与人工原页对照

用合成原页生成 3 页扫描式 PDF：轻微旋转、椒盐/高斯噪声、JPEG 压缩后嵌入 PDF；
内容为已核对的事实（参数表、告警/Byte3、住宿上限/温度）。

| 页 | 原页事实 | OCR 输出 | 人工核对 |
| --- | --- | --- | --- |
| 1 | `20 / Input Monitor Time / ms` | `20`、`Input Monitor Time`、`ms` 均在 | 通过；同行 `/` 被读成 `1`（小瑕疵） |
| 2 | `2,1：Unknown Command`、`Byte3 默认 2` | 两段均正确 | 通过 |
| 3 | `每日上限 400 元`、`18-27 摄氏度` | `400元`、`18-27 摄氏度` 均在 | 通过 |

OCR 三页状态均为 `ok`、无失败页；这是“干净”端到端正路径，不覆盖 180°/复杂表格。

## 隔离导入与引用/页码核对

- 原始 PDF 与 OCR 结果经 `scripts/import_ocr_to_rag.py` 导入隔离库：
  - 3/3 页导入、无 failed/suspect；chunk 页码 1/2/3；
  - OCR JSON 的 `source.sha256` 与原件一致，上传副本/原文件 SHA 一致；
  - 报告 `mapping_ok`、`source_ok`、`failed_page_in_sources=false` 均为真；
- 三个问题均要求“同一来源同时命中页码与关键字”（I5 规则）：

| 问题 | 期望页/关键字 | 结果 |
| --- | --- | --- |
| Input Monitor Time 的索引和单位？ | 页 1 / `ms` | `expectation_hit=true` |
| 告警代码 2,1 含义和 Byte3 默认值？ | 页 2 / `Unknown Command` | `expectation_hit=true` |
| 出差住宿每日上限？ | 页 3 / `400元` | `expectation_hit=true` |

检索耗时约 0–15 ms（本地真实嵌入，隔离库）。证据：
`.verify/ocr-e2e-20261021/import-report.json`。

## 回答支持性（本地真实模型）

用同一批检索来源构造固定证据提示，调用本地 `qwen3:4b-instruct`（temperature=0），
只检查“回答是否包含来源事实且引用编号存在”，不把 3 题当准确率：

| 问题 | 回答（摘要） | 含事实 token | 有引用 |
| --- | --- | --- | --- |
| Input Monitor Time 的索引和单位？ | 索引 20，单位 ms [1] | 是 | 是 |
| 告警代码 2,1 含义和 Byte3 默认值？ | Unknown Command；默认值 2 [1] | 是 | 是 |
| 出差住宿每日上限？ | 400 元 [1] | 是 | 是 |

检索来源均能在同一页找到对应事实；三题回答与来源一致。证据：
`.verify/ocr-e2e-20261021/answer-report.json`（含完整回答、来源、检索毫秒）。

## 管理页提示

本 E2E 样本三页均 `ok`，管理页不会出现 OCR 可疑/失败提示；提示路径已在此前的
隔离样本单独验证（`.verify/ocr-import-20261021-round2/ui-admin-ocr-hint.png`）：
可疑页/失败页提示、`OCR 待核对`、普通重建禁用。本 E2E 未重复做浏览器提示截图。

## 证据与可复现

- `.verify/ocr-e2e-20261021/scanned-e2e.pdf`（扫描式样本）
- `.verify/ocr-e2e-20261021/ocr2/scanned-e2e.ocr.json`（真实 RapidOCR 输出）
- `.verify/ocr-e2e-20261021/import-report.json`（隔离导入/引用/页码）
- `.verify/ocr-e2e-20261021/answer-report.json`（本地模型回答支持性）

## 不能声称的结论

- 样本是**合成扫描式页面**，不是用户真实私有扫描件；真实扫描件仍需按同一流程验收；
- 只覆盖 3 页、3 个问题、1 个本地模型；不是整体回答正确率、误拒答率或引用支持率；
- 未验证 180°、复杂表格、低质量/手写扫描；OCR 仍有小错（页 1 的 `/`→`1`）；
- 未改生产 schema/索引/服务，未启用 CPU rerank，生成侧仍是小样本证据。
