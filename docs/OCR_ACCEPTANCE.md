# OCR 隔离验收：原页对照、旋转行序、表格、混合 PDF、追溯

日期：2026-10-21（第二轮，修复 Codex O1–O4 后）
工具：`scripts/ocr_preprocess.py`（独立工具，未改动生产索引/数据库/服务）。
样例：全部为合成图/PDF，原页由脚本生成，已知原文与版式。
引擎：RapidOCR 3.x（PP-OCRv6 small，CPU）；同一引擎、同一选项跑基线/修复两轮。

## 方法与证据（两轮都保留）

- 验收脚本：`tests/ocr_acceptance_check.py`（真实引擎；缺 OCR 依赖时 SKIP）。
- 基线：HEAD `019b7fb`（本轮修复前）在干净 worktree 运行：
  `.verify/ocr-acceptance-20261021-round2-baseline/`（`failures` 7 条，退出 1）。
- 修复后：当前工作树运行：
  `.verify/ocr-acceptance-20261021-round2/`（`failures: []`，`partial: true` 仅因 180° 已知限制）。
- 第一轮验收证据也保留：`.verify/ocr-acceptance-20261021/`、`...-baseline/`。
- 证据目录规则：已存在且非空的 `--evidence` 目录**拒绝重跑**（退出 2），不删除上一轮
  原页/失败输出；省略 `--evidence` 时使用新的唯一临时目录。`tests/ocr_acceptance_evidence_check.py`
  覆盖拒绝与保留。
- 不触碰生产 DB、索引、服务；不上传任何私有文档。

## 原页对照结果（基线 → 修复后）

| 用例 | 基线 | 修复后 |
| --- | --- | --- |
| 旋转 90° 段落 | 行序反转（rotation=0） | **行序正确**（rotation=270） |
| 旋转 270° 段落 | 行序反转（rotation=0） | **行序正确**（rotation=90） |
| 旋转 90° 表格 | 行列顺序错误（rotation=0） | **行列顺序正确**（rotation=270） |
| 旋转 270° 表格 | 行列顺序错误（rotation=0） | **行列顺序正确**（rotation=90） |
| 旋转 180° 段落 | 行序反转 | 仍反转（已知限制，`partial=true`） |
| 数字/单位（段落） | 400/元/18-27/摄氏度全在 | 全在 |
| 正向 3×3 表格 | 行列顺序正确 | 行列顺序正确 |
| 同页“文字页眉 + 扫描正文” | 只保留页眉、正文缺失、页面 `ok` 且无 warning | **页眉+正文合并**、`mixed_page_ocr_merged` warning、页面 `suspect` |
| 跨页混合 PDF | 3/3 页、页码 1/2/3 | 3/3 页、页 1 text_layer + 页 2/3 OCR |
| 原文件/页码追溯 | filename/SHA-256/页码/`\f` 通过 | 全部通过 |

修复后 `failures: []`；180° 仍失败，列入 `known_limitations`，因此汇总显式 `partial: true`，
**不能称为全部旋转行序验收通过**。

### 耗时（同一引擎/选项，单文件含样例生成/渲染）

| 用例 | 基线 | 修复后 |
| --- | --- | --- |
| 旋转 90° 段落 | ~0.9 s | **1.70 s** |
| 旋转 270° 段落 | ~0.3 s | **1.05 s** |
| 旋转 90°/270° 表格 | ~0.3 s | 1.15–1.16 s |
| 正向 3×3 表格 | ~0.36 s | 0.34 s |
| 同页混合（页眉+扫描正文） | ~0.02 s（未 OCR 正文） | **1.11 s**（OCR 正文） |
| 跨页混合 3 页 | ~0.66 s | 1.41 s |

旋转/混合页的耗时增加是“行序正确 + 正文不漏”的代价，属离线预处理成本，不隐藏。

## 本轮修复（对应复核 O1–O4）

1. **O1 旋转表格列顺序**：不再按精确 `(y_center, x_center)` 排序；转正后的引擎原始顺序
   已正确，直接保留（同一行 y 中心可能只差 0.5，精确排序会打乱列）。新增旋转表格
   90°/270° 原页对照。
2. **O2 旋转候选空/低分不能覆盖首轮**：旋转候选必须非空、字符数不明显变少、置信度不明显
   变差才允许替换；否则保留首轮高置信文字并标记
   `rotation_candidate_rejected` / `page_orientation_unverified`（页面 `suspect`）。
   离线控制：空旋转、0.1 分旋转都不覆盖 0.98 分原文。
3. **O3 同页文字页眉 + 扫描正文**：PdfPageRenderer 计算图像对象覆盖面积；文字层可用但
   图像覆盖 ≥25% 时视为混合页：OCR 图像正文并与原生文字合并，保留 `text_layer_text` 审计，
   加 `mixed_page_image_detected` / `mixed_page_ocr_merged`，页面标记 `suspect`，不再显示
   为完整成功；无 OCR 引擎时保留原生文字并加 `mixed_page_ocr_unavailable`。
4. **O4 证据不覆盖**：验收脚本拒绝非空证据目录（退出 2），默认使用新的唯一临时目录；
   旧原页/失败输出不再被 `rmtree`。新增 `tests/ocr_acceptance_evidence_check.py`。

## 未解决 / 边界

- **180° 页行序**：文字识别正确，但无可靠方向信号（引擎对正/倒都给高分），自动校正需要
  人工方向标记或独立方向分类器；本报告显式标记 `partial: true`，失败证据保留。
- 复杂表格/多栏仍不做版面重建；本验收只证明简单网格及旋转网格样例。
- 合成样例的字符召回 ≠ 业务回答正确率；本轮没有生产索引/服务改动，也没有重处理旧文档。
- 未跑真实私有扫描件；该验收只能证明工具在已知版式上的行为，不代表全库准确率。

## 复跑

```powershell
# 需要带 OCR 依赖的独立环境；证据目录必须为新目录
python tests/ocr_acceptance_check.py --evidence <新证据目录>
python tests/ocr_preprocess_check.py       # 18/18，离线假引擎
python tests/ocr_preprocess_live_check.py  # 38/38，真实引擎合成样例
python tests/ocr_acceptance_evidence_check.py  # 5/5，证据目录保护
```
