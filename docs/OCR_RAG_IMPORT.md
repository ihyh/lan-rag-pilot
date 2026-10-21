# 隔离测试库导入：OCR 正文、引用文件与页码核对

日期：2026-10-21（复核修复轮次）
范围：**只在隔离测试库验证**，不接入上传 API、不改生产库/索引/生成逻辑、不部署。
OCR 工具与 RAG 服务仍保持独立。

## 目的

1. OCR 恢复的正文进入 RAG 后，检索返回的 `content` 是否等于入库 chunk；
2. 引用能否回到**原文件**和**正确页码**，且来源属于**本次导入的文档**；
3. suspect/failed 页是否可见、failed 页是否被排除，以及报告/原件是否可能被误覆盖。

## 集成验证工具（`scripts/import_ocr_to_rag.py`）

- 只允许把 DB/上传目录写到工作区 `.tmp-tests` 下，拒绝生产路径；
- **原件保护**：`--report` 必须是新文件，且不能与原件/OCR JSON/DB/上传目录路径相同；
  报告用排他创建保存，绝不覆盖原件或旧报告；
- **来源绑定**：要求 OCR JSON 带非空、64 位十六进制 `source.sha256`，并与原件字节一致；
  复制后、写报告前再次核对原件 SHA；缺失/空/错误一律拒绝，不建 DB；
- **物理页码**：读取原 PDF 真实页数；OCR 页码必须为正整数、唯一、在物理范围内并覆盖全部页；
  状态只允许 `ok/suspect/failed`；重复页/越界/缺页/未知状态直接拒绝；
- **失败页**：failed 页不进入 chunk；文档 tags 记录 `ocr_ok_pages` / `ocr_suspect_pages` /
  `ocr_failed_pages`，报告列出 `skipped_failed`；
- **来源归属**：单文件验证时检索固定限定本次 `document_id`；返回来源必须与 DB 的
  document/page/content/filename 全部一致；
- **同源验收**：页码与关键字必须在**同一个来源片段**上同时满足，不能用“列表里分别有人
  命中”拼接成功；
- 原始 PDF 原样保存为来源文件，`documents.sha256/stored_name` 指向原文件；
- 不新增生产 schema、不上传界面、不改变现有检索/生成行为。

这不是生产导入方案；用于先证明隔离副本里的正文/引用/页码正确，再由复审决定是否产品化。

## 首轮证据（复核修复后，真实本地嵌入，隔离 DB）

样例：3 页 `mixed.pdf` 的已核对 OCR 输出（页 1 `ok`、页 2 `suspect`、页 3 `ok`）：

- 来源 SHA-256 `dc8253cb…caad1a1`，报告记录 `pdf_pages=3`；
- 导入 3/3 页，chunk 页码 1/2/3；tags `ocr_suspect_pages=2`；
- 两个检索问题（400 元 / 18-27 摄氏度）均 `page_hit` + `token_hit`，且页码和关键字来自
  **同一来源**；`mapping_ok`、`source_ok` 为 true，`failed_page_in_sources=false`；
- failed 控制：页 2 标记 failed 后只导入 1/3 页，页 2 列 `skipped_failed`、tags 含
  `ocr_failed_pages=2`，无页 2 chunk，检索页 3 仍命中；
- 证据：`.verify/ocr-import-20261021-round2/normal-report.json`、`failed-report.json`，
  同目录保留 `mixed.pdf` 与 OCR JSON；首轮旧证据目录未改写。

### 复核反例（真实 CLI）

| 反例 | 结果 |
| --- | --- |
| `--report` 指向原件 | 退出 1；原件 SHA 不变，不建 DB |
| `--report` 指向已有报告 | 退出 1；旧报告字节不变 |
| source SHA 缺失/空/错误 | 退出 1；不建 DB |
| OCR 页码 7（原 PDF 3 页） | 退出 1；不建 DB |
| OCR 重复页 1（failed+ok） | 退出 1；不合并、不覆盖状态 |
| 页码 1 + 关键字只在页 2 | 退出 1；`page_hit/token_hit` 分别 true 但 `expectation_hit=false` |
| 同库先导入旧文档，再验新文档 | 检索来源只属于新 `document_id`，旧文档不参与 |

证据：`.verify/ocr-import-20261021-round2/controls/summary.json`、`i1.log`–`i5b.log`；
real I4 报告 `i4-old-report.json` / `i4-new-report.json`。

## 状态可见性（重要更正）

suspect/failed 页状态写入**文档 tags 与导入报告**，可通过文档 API/报告读取；
**当前管理页面 `app/static/js/admin.js` 的文档列表不渲染 tags**，页面仍显示文档
`ready/就绪`。因此不能声称用户已能在管理 UI 直接看到 suspect/failed 页。
UI 可见性是后续产品化小提交，需单独验收。

## 验证命令

```powershell
# 需要本地嵌入模型；DB/上传目录/报告必须在工作区 .tmp-tests 下
python scripts/import_ocr_to_rag.py `
  --ocr-json <已核对.ocr.json> --source <原文件.pdf> `
  --db .tmp-tests\ocr-import\rag.db --upload-dir .tmp-tests\ocr-import\uploads `
  --report .tmp-tests\ocr-import\report.json `
  --question "..." --expect-page 3 --expect-token "..." `
  --embedding-backend st --embedding-model C:\rag-personal\models\bge-small-zh-v1.5
```

离线回归：`python tests/ocr_import_check.py` —— **29 项断言**（mock 嵌入；物理页数、
重复/越界/缺页、SHA 变体、报告别名、failed/suspect 可见性、来源归属、同源页码+关键字、
原文件 SHA、旧文档隔离）。`run_checks.py` 当前 **46/46**。

## 未做 / 边界

- 未接入真实上传 API/后台管理；不修改生产 schema/检索/生成逻辑；
- tags 只在 API/报告中可见，管理页面尚未渲染；suspect 页仍会入库，是否阻断/降权需产品决定；
- 不重处理旧文档、不重建生产索引；180°/复杂版式等 OCR 限制仍在；
- 未运行生成侧答案对照；`fact_correct`/`citation_supported` 仍无结论。
