# 隔离测试库导入：OCR 正文、引用文件与页码核对

日期：2026-10-21
范围：**只在隔离测试库验证**，不接入上传 API、不改生产库、不重建生产索引、不部署。
OCR 工具与 RAG 服务仍保持独立；本文件记录第一次端到端导入核对的集成差异和首轮证据。

## 目的

回答三个问题：

1. OCR 恢复的正文进入 RAG 后，检索返回的 `content` 是否还等于入库 chunk；
2. 引用能否回到**原文件**（不是 OCR 中间文件）和**正确页码**；
3. suspect/failed 页是否在导入结果里**可见**，而不是被静默当成正常页。

## 实现（`scripts/import_ocr_to_rag.py`，集成验证工具）

- 只允许把 DB/上传目录写到工作区 `.tmp-tests` 下，**拒绝生产路径**；
- 校验 OCR JSON 的 `source.sha256` 与原始 PDF 字节一致，不一致拒绝导入；
- 原始 PDF 原样复制为来源文件，`documents.sha256/stored_name` 指向原文件；
- `pages[]` 的 `ok`/`suspect` 页按真实页码切块；`failed` 页不进入 chunk；
- 文档 `tags` 写入 `ocr_ok_pages=` / `ocr_suspect_pages=` / `ocr_failed_pages=`，让导入后
  在现有文档 API/UI 的 tags 中可见；
- 报告保留每页状态/warnings、chunk→page 映射、检索来源的 content/page/filename 三重核对；
- 不新增生产 schema、不上传界面、不改变现有检索/生成行为。

这不是生产导入方案；它用于在隔离副本里先证明映射正确，再由复审决定是否产品化。

## 首轮证据（真实本地嵌入，隔离 DB）

样例：`mixed.pdf`（3 页）经 OCR 工具处理后的**已核对输出**：

- 来源 SHA-256：`dc8253cb…caad1a1`；页状态：页 1 `ok`（text_layer）、页 2 `suspect`
  （rotated_to_read）、页 3 `ok`（table OCR）；
- 导入：3/3 页，chunk 页码 1/2/3；tags 含 `ocr_suspect_pages=2`；
- 检索问题 1「每日上限 400 元是多少？」：`page_hit=true`、`token_hit=true`；
- 检索问题 2「温度应保持在 18-27 摄氏度」：`page_hit=true`、`token_hit=true`；
- 所有返回来源的 `content`、`page`、`filename` 与 DB/原文件一致
  （`mapping_ok=true`），`failed_page_in_sources=false`，原 PDF SHA 保留
  （`source_ok=true`）；检索约 14–16 ms。
- 证据：`.verify/ocr-import-20261021/import-report2.json`（原始 `mixed.ocr.json`、
  `mixed.pdf` 同目录）。

### failed 页可见性控制

把同一 OCR 结果的页 2 标记为 failed 后再导入：

- 只导入页 1/3，chunk 页码只有 1/3；页 2 明确列在 `skipped_failed`，没有 chunk；
- tags 含 `ocr_failed_pages=2`；检索仍能命中页 3，且来源没有 failed 页；
- `mapping_ok=true`、`source_ok=true`、`failed_page_in_sources=false`；
- 证据：`.verify/ocr-import-20261021/import-report-failed2.json`。

首轮证据均已保留；重复运行使用新的隔离目录，不覆盖旧报告。以上结果只证明
“正文/引用/页码映射”和可视状态，不证明模型回答正确率。

## 验证命令

```powershell
# 需要本地嵌入模型；DB/上传目录必须在工作区 .tmp-tests 下
python scripts/import_ocr_to_rag.py `
  --ocr-json <已核对.ocr.json> --source <原文件.pdf> `
  --db .tmp-tests\ocr-import\rag.db --upload-dir .tmp-tests\ocr-import\uploads `
  --report .tmp-tests\ocr-import\report.json `
  --question "..." --expect-page 3 --expect-token "..." `
  --embedding-backend st --embedding-model C:\rag-personal\models\bge-small-zh-v1.5
```

离线映射回归：`python tests/ocr_import_check.py` —— 13 项断言（mock 嵌入、
suspect/failed 可见性、原文件 SHA、拒绝 SHA 不一致的导入）。

## 未做 / 边界

- 未接入真实上传 API/后台管理，不修改生产 schema/检索/生成逻辑；
- suspect 页会入库但仅通过 tags/报告提示，不阻止检索；是否阻断或降权需产品决定；
- 不重处理旧文档、不重建生产索引；复杂版式/180° 等 OCR 已知限制仍在；
- 未运行生成侧答案对照；`fact_correct`/`citation_supported` 仍无结论。
