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
```

常用参数：`--min-text-chars`（文字层可用阈值，默认 40 个非空白字符）、`--dpi`（扫描页渲染，
默认 200）、`--auto-rotate/--no-auto-rotate`、`--pages`、`--max-pages`、`--overwrite`、`--json`。

退出码：`0` 成功（可有可疑页）；`1` 存在失败页/失败文件；`2` 参数错误（argparse）。

## 输出

输出目录写入三类文件：

1. `<stem>.ocr.json`（权威结果）：工具/版本、时间、引擎与模型、选项、来源摘要、
   逐页记录、汇总、状态。
2. `<stem>.ocr.txt`：逐页纯文本，用换页符 `\f` 分隔；第 N 页 = 第 N 段（JSON 里的
   `pages[].page` 是权威页码映射）。
3. `ocr_run.json`：本次运行清单（每个输入的状态、输出路径、SHA-256、汇总、退出码）。

逐页记录字段：

| 字段 | 含义 |
| --- | --- |
| `page` | 页码（1 起） |
| `source` | `text_layer`（原始文字层）/ `ocr` / `none` |
| `status` | `ok` / `suspect` / `failed` / `skipped` |
| `text` / `chars` | 该页文本与非空白字符数 |
| `warnings` | `low_confidence`、`garbled_text`、`no_text_detected`、`short_text`、`text_layer_suspect_fallback`、`rotated_to_read`、`not_selected`、`page_limit_reached` 等 |
| `confidence` | 引擎提供逐行分数时才有：`{mean, min, lines}`；没有可靠分数时**不写该字段，不伪造** |
| `uncertain_lines` | 低于 `--line-score` 的行（行号、分数、文本），供人工核对 |
| `rotation_degrees` / `rotation_attempts` | 识别方向与尝试记录（首轮不可靠时尝试 90/180/270 度取最佳） |
| `text_layer_text` | 仅在文字层疑似乱码、回退 OCR 时保留原始文字层供审计 |
| `error` | 失败页/失败文件的 `code` 与 `message` |

汇总 `summary`：`pages / text_layer_pages / ocr_pages / skipped_pages / ok_pages /
suspect_pages / failed_pages`。文件整体 `status`：`ok` / `partial` / `failed`。

## 行为保证

- **按页判断**：每页分别看文字层（非空白字符数 + 乱码比例），不是只看整份文件总字数。
- **保留可用文字层**：可用页不调用 OCR；混合 PDF 只 OCR 需要 OCR 的页。
- **不覆盖原件**：只读输入，输出写到 `--out`；处理前后比较 SHA-256，原件变化会标记
  `source_modified_detected` 并判失败。已有同名输出默认拒绝（`--overwrite` 才覆盖）。
- **区分原始文字与 OCR**：`source` 字段区分；乱码回退时保留 `text_layer_text`。
- **不伪造置信度**：引擎无分数就不写 `confidence`/`uncertain_lines`。
- **失败不静默**：单页失败只影响该页并记 `failed` + 错误码；可疑页记 `suspect`；
  文件有失败页时退出码 1，绝不显示为完整成功。

## 验证方法

- 离线回归（不需要 OCR 依赖，注入假引擎/假渲染器）：
  `python tests/ocr_preprocess_check.py` —— 12 组，覆盖按页分流、混合 PDF、
  无置信度不伪造、低置信度可疑页、乱码文字层回退、单页失败、旋转兜底、页码选择与上限、
  原件不变、拒绝覆盖、损坏 PDF/图片/同名/不支持格式、CLI `--engine none`。
- 真实引擎样例检查（需要 `requirements-ocr.txt`；缺依赖时自动 SKIP 并退出 0）：
  `python tests/ocr_preprocess_live_check.py` —— 30 项，使用合成中文/英文/数字单位/
  表格多栏/旋转页与文字+扫描混合 PDF，对照已知原文计算字符召回并验证：
  - 混合 PDF：`text_layer + ocr` 分流正确；
  - 中文样例字符召回 1.000、英文 1.000，`400/18/27` 数字正确；
  - 表格/多栏：`左列/右列/机房温度/UPS/每季度` 全部保留；
  - 旋转页：字符召回 1.000（引擎自带方向分类在 0 度即读对；工具旋转兜底另有离线用例）；
  - 置信度保留、原件 SHA-256 不变、页码映射正确、各文件无失败页。

### 人工对照原图发现的差异（真实、未修正）

用合成样例对照原图逐项核对，实际观察到：

- **旋转页**：3 行文字全部识别正确，但**行序反转**（原图从上到下为「设备巡检记录 / 出差住宿
  每日上限 400 元 / 温度应保持在 18-27 摄氏度」，OCR 输出为倒序）。适合检索字符，但不能
  直接当作段落原文；接入时应保留旋转/顺序警告或人工核对。
- **表格/多栏**：两行两列样例中，第 1 行被合并成一行（「左列… 右列…」），第 2 行被拆成两行
  （先左右合并后右列另起一行）。未做版面重建，行列可能串联；数字与单位本身正确。
- 中英文之间的空格可能丢失/增加（如「每日上限400元」「18-27 摄氏度」），字符内容正确。

这些差异会进入输出 JSON 的 `warnings`（如 `rotated_to_read`）或需人工核对；工具不会把
不确定的值补写成确定事实。合成样例的召回率**不代表**真实私有文档的业务准确率。

## 未做 / 边界

- 未接入导入界面、未重处理旧文档、未重建索引（按交接要求另行提交，需备份与历史引用兼容性说明）。
- 未对用户全部私有文档逐页审计；本轮只验证合成样例。
- 旋转只尝试 90° 倍数；复杂透视/低质量扫描未评估。
- 不执行任意命令、不删除文件；工具只读输入、写自己的输出目录。
