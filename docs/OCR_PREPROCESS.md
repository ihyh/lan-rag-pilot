# OCR 预处理小工具（第二轮）

状态：**独立工具已实现并验证；尚未接入导入界面，未重处理旧文档，未重建索引。**

对应交接文档「第二轮：OCR 预处理小工具」。工具位于 `scripts/ocr_preprocess.py`，
OCR 依赖单独放在 `requirements-ocr.txt`，**不属于默认安装**，应用服务不会导入本工具。

## 为什么做

现有 `app/parsing.py` 的 `parse_pdf` 只抽取 PDF 文字层：整份文件没有文字就报 `scanned_pdf`，
部分页有文字时无文字页不产生 `Unit`。因此扫描页/混合 PDF 可能漏页。本工具先解决
「按页判断 + 本地 OCR + 可核对输出」，导入集成与旧索引迁移另行提交。

## Windows / CPU 可运行方案

- Python 3.12（与项目默认一致），纯 CPU 推理，无 GPU 依赖。
- 引擎：**RapidOCR 3.10**（ONNX Runtime 后端，PP-OCRv6 小模型，随包分发，首次运行不需要联网下载）。
- 依赖（`requirements-ocr.txt`）：`rapidocr`、`onnxruntime`、`opencv-python-headless`、
  `pypdfium2`、`Pillow`、`pypdf`。
- 体积（本机实测 site-packages）：rapidocr **31.6 MB**（含模型）、onnxruntime **45.5 MB**、
  opencv-headless **112 MB**、Pillow 15.4 MB、pypdfium2 0.4 MB、pypdf 2.7 MB，
  **合计约 210 MB**；模型文件 3 个共约 31.8 MB（det 9.93 MB / rec 21.23 MB / cls 0.59 MB）。
- 安装（推荐独立环境，避免影响默认安装）：

```powershell
py -3.12 -m venv .venv-ocr
.venv-ocr\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

### 引擎选择说明

评估过 PaddleOCR（官方快速开始见交接文档链接），但其 Windows CPU 方案需要
`paddlepaddle`（体积更大、Windows 轮子与 Python 版本耦合更紧），且模型需另外下载。
本轮按必要性只接入 **一个** 引擎：RapidOCR（PP-OCRv6 小模型 + onnxruntime），
中文/英文同模型、Windows 轮子完整、模型随包分发。没有同时接第二套引擎。

## 用法

```powershell
# 处理扫描 PDF / 图片 / 混合 PDF（逐页判断）
.venv-ocr\Scripts\python.exe scripts\ocr_preprocess.py 扫描件.pdf 图片.png --out out\ocr

# 只处理部分页；关闭旋转兜底；只保留可用文字层（不 OCR）
.venv-ocr\Scripts\python.exe scripts\ocr_preprocess.py 混合.pdf --out out\ocr2 --pages 1-3,7
.venv-ocr\Scripts\python.exe scripts\ocr_preprocess.py 混合.pdf --out out\ocr3 --no-auto-rotate
.venv-ocr\Scripts\python.exe scripts\ocr_preprocess.py 文本.pdf --out out\ocr4 --engine none

# 文字页存在 3%–15% 图像正文、需要显式恢复正文时
.venv-ocr\Scripts\python.exe scripts\ocr_preprocess.py 页眉加小图正文.pdf --out out\ocr5 --force-mixed-ocr
```

常用参数：`--min-text-chars`（文字层可用阈值，默认 40 个非空白字符）、`--dpi`（扫描页渲染，
默认 200）、`--auto-rotate/--no-auto-rotate`、`--pages`、`--max-pages`、`--overwrite`、`--json`、
`--force-mixed-ocr`（对 3%–15% 图像覆盖的文字页也强制混合页 OCR）。

图片输入支持单帧 PNG/JPG/BMP/WebP，以及**多帧 TIFF**：多帧 TIFF 逐帧处理，页码/页选择/
页数上限与 PDF 一致，不会静默只取第一帧；某一帧解码失败只把该页标记失败。

退出码：`0` 成功（可有可疑页）；`1` 存在失败页/失败文件；`2` 参数错误（argparse）。

## 输出

输出目录写入三类文件：

1. `<stem>.ocr.json`（权威结果）：工具/版本、时间、引擎与模型、选项、来源摘要、
   逐页记录、汇总、状态。
2. `<stem>.ocr.txt`：逐页纯文本，用换页符 `\f` 分隔；第 N 页 = 第 N 段（JSON 里的
   `pages[].page` 是权威页码映射；多帧 TIFF 同样逐帧对应）。
3. `ocr_run.json`：本次运行清单（每个输入的状态、输出路径、SHA-256、汇总、退出码）。

**运行清单与覆盖语义**：输出文件默认拒绝覆盖（`output_exists`）。清单也遵循同一语义：
首次运行写 `ocr_run.json`；目录里已有清单且没有 `--overwrite` 时，本次运行改为写
唯一的 `ocr_run-<时间戳>[-N].json`，**绝不改写旧清单**（便于保留先前批次的成功/失败证据，
同一目录处理新一批输入也不会丢失旧来源映射）。`--overwrite` 时才允许覆盖固定名清单。

逐页记录字段：

| 字段 | 含义 |
| --- | --- |
| `page` | 页码（1 起） |
| `source` | `text_layer`（原始文字层）/ `ocr` / `none` |
| `status` | `ok` / `suspect` / `failed` / `skipped` |
| `text` / `chars` | 该页文本与非空白字符数 |
| `warnings` | `low_confidence`（页均值低）、`low_confidence_line`（存在低于行阈值的行）、`garbled_text`、`no_text_detected`、`short_text`、`text_layer_suspect_fallback`、`rotated_to_read`、`not_selected`、`page_limit_reached` 等 |
| `confidence` | 引擎提供逐行分数时才有：`{mean, min, lines}`；没有可靠分数时**不写该字段，不伪造** |
| `uncertain_lines` | 低于 `--line-score` 的行（行号、分数、文本），供人工核对 |
| `rotation_degrees` / `rotation_attempts` | 识别方向与尝试记录（首轮不可靠时尝试 90/180/270 度取最佳） |
| `text_layer_text` | 仅在文字层疑似乱码、回退 OCR 时保留原始文字层供审计 |
| `error` | 失败页/失败文件的 `code` 与 `message` |

汇总 `summary`：`pages / text_layer_pages / ocr_pages / skipped_pages / ok_pages /
suspect_pages / failed_pages`。文件整体 `status`：`ok` / `partial` / `failed`。

## 行为保证

- **按页判断**：每页分别看文字层（非空白字符数 + 乱码比例），不是只看整份文件总字数。
- **保留可用文字层**：可用页默认不调用 OCR；但若页面存在大块图像（覆盖 ≥15%），视为
  “文字页眉 + 扫描正文”混合页：保留原生文字并 OCR 图像正文合并，页面标记 `suspect`；
  3%–15% 图像不静默成功，标记 `page_image_content_unverified`，需要正文时用
  `--force-mixed-ocr` 明确入口；无 OCR 引擎时保留原生文字并提示
  `mixed_page_ocr_unavailable`。混合页渲染/识别异常按单页失败处理并继续后续页，退出码 1。
- **旋转页方向**：文字框明显竖直时触发 90/270/180 尝试；旋转候选必须非空、字符数不明显
  变少、置信度不明显变差才允许替换首轮文字，否则保留首轮并标记
  `rotation_candidate_rejected` / `page_orientation_unverified`。转正后直接使用引擎原始
  行序，不按精确 y 重排（避免同一表格行的列顺序被打乱）。
- **多帧图片逐帧处理**：多帧 TIFF 按帧输出页码，遵守 `--pages`/`--max-pages`；单帧图片行为不变。
- **不覆盖原件**：只读输入，输出写到 `--out`；处理前后比较 SHA-256，原件变化会标记
  `source_modified_detected` 并判失败。已有同名输出默认拒绝（`--overwrite` 才覆盖）。
- **清单不被无声覆盖**：未经 `--overwrite` 时旧 `ocr_run.json` 保持字节不变，新运行写唯一清单。
- **区分原始文字与 OCR**：`source` 字段区分；乱码回退时保留 `text_layer_text`。
- **不伪造置信度**：引擎无分数就不写 `confidence`/`uncertain_lines`。
- **低分行必须被看见**：只要存在低于 `--line-score` 的行，页面就加 `low_confidence_line`
  并置 `suspect`（即使页面均分仍高），不把低分数字/单位行藏在 `uncertain_lines` 里。
- **失败不静默**：单页失败只影响该页并记 `failed` + 错误码；可疑页记 `suspect`；
  文件有失败页时退出码 1，绝不显示为完整成功。

## 验证方法

- 离线回归（不需要 OCR 依赖，注入假引擎/假渲染器）：
  `python tests/ocr_preprocess_check.py` —— **18 组**，覆盖按页分流、混合 PDF、
  无置信度不伪造、低置信度可疑页、**高均分+低分关键行（R6）**、乱码文字层回退、
  单页失败、旋转兜底、**旋转页/旋转表格行序（不按精确 y 重排）**、**旋转空/低分候选不覆盖
  首轮高置信文字**、**同页文字页眉+扫描正文：15% 自动阈值 / 3%–15% 待核对 /
  --force-mixed-ocr 强制入口 / 混合页渲染与 OCR 异常继续后续页并退出 1**、
  页码选择与上限、**多帧 TIFF 逐帧/页选择/上限/帧失败（R5）**、原件不变、拒绝覆盖、
  **运行清单覆盖语义（R7）**、损坏 PDF/图片/同名/不支持格式、CLI `--engine none`。
- 真实引擎样例检查（需要 `requirements-ocr.txt`；缺依赖时自动 SKIP 并退出 0）：
  `python tests/ocr_preprocess_live_check.py` —— **38 项**，使用合成中文/英文/数字单位/
  表格多栏/旋转页、文字+扫描混合 PDF 与**两帧 TIFF**，对照已知原文计算字符召回并验证：
  - 混合 PDF：`text_layer + ocr` 分流正确；
  - 中文样例字符召回 1.000、英文 1.000，`400/18/27` 数字正确；
  - 表格/多栏：`左列/右列/机房温度/UPS/每季度` 全部保留；
  - 旋转页：字符召回 1.000；工具按文字框方向选择旋转角度，转正后使用引擎原始行序
    （90/270 段落与表格已验证；180 仍为已知限制，见下）；
  - **多页 TIFF：帧数与页码 1/2、两帧都 OCR、两帧文字与第二帧数字/单位均保留**；
  - 置信度保留、原件 SHA-256 不变、页码映射正确、各文件无失败页。
- OCR 隔离验收（原页对照，需真实引擎；缺依赖 SKIP）：
  `python tests/ocr_acceptance_check.py` —— 合成原页/旋转页/表格/混合 PDF 对照，
  结果与失败证据见 [OCR_ACCEPTANCE.md](OCR_ACCEPTANCE.md)。

### 人工对照原图发现的差异（真实、未修正）

用合成样例对照原图逐项核对，实际观察到：

- **旋转页（90/270 已修复）**：原页对照时曾出现 3 行文字全部识别正确但**行序反转**
  （原图从上到下为「设备巡检记录 / 出差住宿每日上限 400 元 / 温度应保持在 18-27 摄氏度」，
  OCR 输出为倒序）。现在工具会读取文字框几何：明显竖直的文字框触发 90/270/180 旋转尝试，
  选中“文字框水平”的轮次，直接使用引擎转正后的原始行序（不再按精确 y 排序，避免把同一
  表格行的列顺序打乱）；隔离验收中 90/270 段落与表格行/列顺序已与原文一致。
- **旋转候选空/低分**：空旋转或明显低分的候选不会覆盖首轮高置信文字；保留首轮并标记
  `rotation_candidate_rejected` / `page_orientation_unverified`（页面 `suspect`）。
- **旋转页（180 未解决）**：文字识别正确，但 180 度页面没有可靠方向信号（引擎对正/倒
  文字都给高分），工具无法自动判断，行序仍为反转；验收汇总显式 `partial: true`，失败证据见
  [OCR_ACCEPTANCE.md](OCR_ACCEPTANCE.md)，需要人工方向标记或独立方向分类器。
- **表格/多栏**：简单网格与旋转网格样例的行列顺序、数字/单位可保持；复杂表格/两栏排版
  不做版面重建，行列仍可能串联（旧两行两列对照中第 2 行会被拆行）；数字与单位本身正确。
- **同页文字页眉 + 扫描正文**：图像覆盖 ≥15% 自动 OCR 并合并原生文字（原反例约 18%
  已可恢复）；3%–15% 标记待核对并可用 `--force-mixed-ocr` 恢复；渲染/OCR 异常记失败页、
  保留原生文字并退出 1；仅页眉的输出不再显示为完整成功。
- 中英文之间的空格可能丢失/增加（如「每日上限400元」「18-27 摄氏度」），字符内容正确。

这些差异会进入输出 JSON 的 `warnings`（如 `rotated_to_read`）或需人工核对；工具不会把
不确定的值补写成确定事实。合成样例的召回率**不代表**真实私有文档的业务准确率。

## 未做 / 边界

- 未接入导入界面、未重处理旧文档、未重建索引（按交接要求另行提交，需备份与历史引用兼容性说明）。
- 未对用户全部私有文档逐页审计；本轮只验证合成样例。
- 旋转只尝试 90° 倍数；180° 页面行序仍无可靠自动判断（已知限制，需人工/方向分类器）；
  复杂透视/低质量扫描未评估。
- 同页混合检测按图像对象覆盖面积估算（阈值 25%）；复杂版式/图表页仍可能需人工核对，
  不保证所有“文字+图像”页都被识别为混合页。
- 多帧仅支持 TIFF；其它格式按单帧图片处理，不在支持后缀内的输入会被拒绝。
- 不执行任意命令、不删除文件；工具只读输入、写自己的输出目录。
