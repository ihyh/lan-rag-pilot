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

## 实际 RAG 接口（隔离服务）

在导入库的**隔离副本**（`.tmp-tests/ui-e2e-api/data/rag.db`，原导入库未改写）上启动
临时 uvicorn：真实本地 `bge-small-zh-v1.5` 嵌入、本地 `qwen3:4b-instruct`、CPU、无生产
数据；通过真实登录和 `POST /api/query` 调用（`scope_mode=all`），验证 HTTP 链路而不是
直接调用 helper：

| 问题 | API 回答（摘要） | 来源页命中 | 核心事实 token | 引用编号 | 整答支持（人工） |
| --- | --- | --- | --- | --- | --- |
| Input Monitor Time 的索引和单位？ | 索引 20，单位 ms [1] | 页 1 | 是 | 是 | 通过 |
| 告警代码 2,1 含义和 Byte3 默认值？ | Unknown Command；默认值 2 [1] | 页 2 | 是 | 是 | **未通过**：随后又称“无法确认是否为重试次数”，与来源/前句矛盾 |
| 出差住宿每日上限？ | 400 元 [1] | 页 3 | 是 | 是 | 通过 |

- 三次请求 HTTP 200、`status=ok`；来源 `filename=scanned-e2e.pdf` 且页码为 1/2/3 对应；
  **HTTP 执行成功、核心 token 和引用编号存在，不等于整段答案受支持**；
- 第 2 答为“部分事实正确、整答未通过（无依据不确定性/自相矛盾）”，已保留原答案；
- 检索约 0–16 ms；模型耗时约 0.5–6.5 s（含一次较长解释）；
- 证据：`.verify/ocr-e2e-20261021/rag-api-report.json`（原始回答/来源/耗时）；
  严格验收判据：[`ANSWER_ACCEPTANCE.md`](ANSWER_ACCEPTANCE.md)；保存标注：
  `.verify/ocr-e2e-20261021/answer-quality-review.json`（保留原答，分列核心事实/引用/
  无依据扩写/自相矛盾/整答支持）。

## 回答支持性（固定证据直连模型，首轮）

用同一批检索来源构造固定证据提示，调用本地 `qwen3:4b-instruct`（temperature=0），
该段落只检查“回答是否包含来源事实且引用编号存在”，**不是整答正确性验收**：

| 问题 | 回答（摘要） | 含事实 token | 有引用 |
| --- | --- | --- | --- |
| Input Monitor Time 的索引和单位？ | 索引 20，单位 ms [1] | 是 | 是 |
| 告警代码 2,1 含义和 Byte3 默认值？ | Unknown Command；默认值 2 [1] | 是 | 是 |
| 出差住宿每日上限？ | 400 元 [1] | 是 | 是 |

这些字段只是自动信号；已保留原答案，并在
[`ANSWER_ACCEPTANCE.md`](ANSWER_ACCEPTANCE.md) 的判据下把整答支持与无依据扩写分开记录，
不再声称“三题回答均受支持”。

## 管理页提示与全 ok 文档按钮

本 E2E 样本三页均 `ok`，管理页不出现可疑/失败提示；单独截图确认全部页 ok 的 OCR 文档
**“重建索引”按钮也禁用**（普通重建会重新解析原 PDF、使 OCR tags 失真），tooltip 说明需
重新运行 OCR 工具。证据：`.verify/ocr-e2e-20261021/ui-admin-all-ok.png` / `.json`。
可疑/失败页提示的独立截图仍为：
`.verify/ocr-import-20261021-round2/ui-admin-ocr-hint.png`。

## 证据与可复现

- `.verify/ocr-e2e-20261021/scanned-e2e.pdf`（扫描式样本）
- `.verify/ocr-e2e-20261021/ocr2/scanned-e2e.ocr.json`（真实 RapidOCR 输出）
- `.verify/ocr-e2e-20261021/import-report.json`（隔离导入/引用/页码）
- `.verify/ocr-e2e-20261021/rag-api-report.json`（实际 `/api/query` 登录/回答/引用）
- `.verify/ocr-e2e-20261021/ui-admin-all-ok.png` / `.json`（全 ok 文档按钮禁用）
- `.verify/ocr-e2e-20261021/answer-report.json`（固定证据直连模型首轮）

## 不能声称的结论

- 样本是**合成扫描式页面**，不是用户真实私有扫描件；真实扫描件仍需按同一流程验收；
- 只覆盖 3 页、3 个问题、1 个本地模型；不是整体回答正确率、误拒答率或引用支持率；
- 未验证 180°、复杂表格、低质量/手写扫描；OCR 仍有小错（页 1 的 `/`→`1`）；
- 未改生产 schema/索引/服务，未启用 CPU rerank；生成侧仍只有 3 题小样本证据，
  不能代表整体准确率；实际 Web 前端问答界面未做浏览器验收，仅验证了 HTTP `/api/query`。
