# 管理页 OCR 提示独立复核（2026-10-21）

## 结论

复核 `f4a573ce1250413ef1dce14a8278c14d9839e137`，功能分支 `feat/manual-document-scope-20261020`；本地与本轮已核对远端一致。正常导入后的提示通过已测范围，但有一项 P2 集成问题，建议修复后再进入真实扫描件端到端验收。本轮未修改产品实现、部署、提交或推送。

## A1 / P2：普通重建索引后，“失败页未入库”提示可能失真

- 位置：`app/static/js/admin.js:160-163`；相关已有操作在第 138、264 行，后端为 `app/ingest.py::reindex_document/_index_document`。
- 新提示直接把历史 `ocr_failed_pages` tags 解释成当前索引事实；同一行“重建索引”仍可操作。该操作从保存的原始 PDF 普通解析并重建切片，不应用 OCR 失败页排除规则，也不更新 OCR tags。
- 独立反例：原始两页 PDF，第 2 页有原生文字但 OCR JSON 状态为 `failed`。隔离导入后只有 page 1 chunk，tags 包含 `ocr_failed_pages=2`。调用实际重建函数后状态仍 `ready`，chunk 页码变成 `[1, 2]`，tags 却原样保留。因此重新加载列表仍称第 2 页“未入库”，与数据库矛盾；OCR 导入正文也可能被普通解析结果替换。
- 复现使用真实 PDF 解析、真实导入/重建函数、mock 嵌入；只将解析工作进程包装替换成直接调用同一真实 PDF parser，避免进程树测试机制。未调用生产或网络端点，不声称完成浏览器点击全链路。
- 最小建议：测试版先明确阻止 OCR 导入文档走普通重建入口，并告知需通过 OCR 工具重新处理；或者可靠处理重建后的 OCR 元数据失效，且不得继续声称这些页当前未入库。不要仅改一个标签却继续误报 OCR 正文/页状态有效。
- 必须补充回归：导入含 failed/suspect 的 PDF → 重建路径（允许或拒绝）→ 核对当前 chunk、tags 与管理页提示一致。两页原生文字反例应保留。

证据：`data/eval/reports/admin-ocr-hint-review-20261021/reindex-results.json`、`reindex_check.py`、`reindex-import.log`。原始夹具/隔离库在工作区 `.tmp-tests/admin-ocr-reindex-review-20261021`，未改生产索引。

## A2 / P3：页码解析对坏数字过滤不完整（非阻塞建议）

- 位置：`app/static/js/common.js:688-694`。
- `parseInt` 将 `2bad`、`1.5`、`3e2` 分别解释成 `2`、`1`、`3`，且接收不安全的大整数；实际渲染会显示这些伪页码。同一状态重复 tags 逐项覆盖，`["ocr_failed_pages=2", "ocr_failed_pages=bad"]` 最后变成空列表，隐藏已有失败提示。
- 当前导入工具正常生成唯一、整数格式的 tags，已测正常流程不受影响，因此不把该边缘输入当成普通导入失败。若维持“过滤坏页码”的接口约定，建议全串验证正整数并检查 safe integer，重复状态至少不应清掉此前有效页码；补对应测试即可。
- 证据：`render-results.json` 的 `malformedNumeric`、`duplicateStatus` 和 `partial-numeric`。

## 已通过与证据边界

- `node --check` 两个改动 JS 通过。
- 提交的 `admin_ocr_hint_check.js` 及邻接 `form_modal`、`citations`、`device_scope`、`scope_selection`、`sw_cache` 检查通过。
- 执行实际 `common.js/admin.js` 的 `loadDocs`，在最小 DOM 测试替身中核对 5 个正常场景：JSON tags、数组 tags、无 OCR tags、同时 suspect/failed、坏 JSON。中文提示、就绪状态和待核对标识均符合预期。合计 9 条命令退出 0；诊断脚本记录反例成功不代表产品修复通过。
- 开发者保存的 `.verify/ocr-import-20261021-round2/ui-admin-ocr-hint.png` 与 JSON 已读取，截图的两行及标识与交接一致。
- 本轮浏览器工具报告 Work 设置禁用本地浏览器使用，未绕过，也没有独立新浏览器截图或生产登录验收。最小 DOM 不是浏览器布局验收。
- 文件哈希和命令日志保留于 `data/eval/reports/admin-ocr-hint-review-20261021/checks.json`、`01-*` 至 `09-*`；检查前后产品资产未变化。未独立执行完整 `run_checks.py`，不将开发者声称的 47/47 作为本轮独立计数。

## 当前进度与下一步

OCR 预处理与隔离导入已通过前轮已测范围；本轮管理页正常提示可用，但重建生命周期一致性尚未关闭。请 DeepSeek 先修 A1 并补反例回归，A2 可同一小提交处理，然后交新 SHA 复核。通过后再用少量人工核对的真实扫描件验证 OCR → 隔离导入 → 提示 → 检索/引用/页码 → 答案支持性；当前没有整体回答正确率提升的证据，CPU rerank 保持关闭。
