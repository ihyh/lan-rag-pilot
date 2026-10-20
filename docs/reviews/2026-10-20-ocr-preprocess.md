# 第二轮：OCR 预处理小工具 —— 提交说明（待 Codex 复核）

日期：2026-10-20
分支：`feat/manual-document-scope-20261020`（同一分支按轮次独立提交）
本轮基线：`8e05648`（第一轮 R1/R2/R3 修复提交之后）
本轮提交：见本文件所在提交（`feat(ocr)` 开头的 commit）；功能代码不依赖第一轮前端改动。

## 目标与范围

对应交接文档「第二轮：OCR 预处理小工具」：先交付**独立、本地运行**的预处理工具，
不接入导入界面、不重处理旧文档、不重建索引、不新增第二套 RAG。

已交付：

- `scripts/ocr_preprocess.py`：按页判断的 OCR 预处理工具。
- `requirements-ocr.txt`：OCR 依赖单独文件（不属于默认安装）。
- `tests/ocr_preprocess_check.py`：离线回归（注入假引擎/渲染器，不需要 OCR 依赖）。
- `tests/ocr_preprocess_live_check.py`：真实引擎合成样例检查（缺依赖自动 SKIP）。
- `docs/OCR_PREPROCESS.md`：方案、依赖/模型体积、用法、输出 schema、限制。

## 引擎选择

- 评估了 PaddleOCR（交接文档链接），但 Windows/CPU 需要 `paddlepaddle`、体积更大且模型需另下载。
- 按必要性只接入 **一个** 引擎：RapidOCR 3.10（PP-OCRv6 小模型、onnxruntime CPU、模型随包分发，
  首次运行无需联网下载）。
- 模型体积：det 9.93 MB + rec 21.23 MB + cls 0.59 MB ≈ 31.8 MB；连同依赖实测约 210 MB。

## 行为要点（验收点映射）

| 验收点 | 实现/证据 |
| --- | --- |
| 支持扫描 PDF/图片与混合 PDF；按页判断，不能只看总字数 | 逐页 `extract_text` + 非空白字符数/乱码比例；混合 PDF 离线用例 2 页文字层 + 1 页 OCR 分流正确；真实引擎混合 PDF 用例 `['text_layer','ocr']` |
| 保留可用文字层 | 可用页不调用 OCR（离线用例断言引擎调用次数 0） |
| 原件不覆盖；输出文本、页码映射、来源摘要、引擎/版本、状态、失败/疑似乱码页 | 只读输入 + 处理前后 SHA-256 比对；`.ocr.json`（权威）+ `.ocr.txt`（`\f` 分页）+ `ocr_run.json`；`warnings/uncertain_lines/error` |
| 区分原始文字与 OCR；有置信度则保留，没有不伪造 | `source=text_layer|ocr`；乱码回退保留 `text_layer_text`；引擎无分数时省略 `confidence`（离线断言） |
| 中文、英文、旋转页、表格/多栏、数字与单位人工对照原图，记录漏字/数字替换/行列串联 | 真实引擎合成样例 30 项：中文/英文召回 1.000，`400/18/27` 正确；表格两列关键词全部保留；旋转页字符召回 1.000 但**行序反转**；表格两行出现合并/拆分差异（详见 `docs/OCR_PREPROCESS.md` 与下方观察） |
| 不清楚的值标记待核对，不补写成确定事实 | 低分行进入 `uncertain_lines`；可疑页 `status=suspect` + `warnings` |
| 失败页不能显示为无警告的完整成功 | 失败页 `status=failed` + `error.code`；文件 `partial/failed`；退出码 1 |
| 小样本导入测试副本、引用定位原文件与页；正常文字 PDF 不明显退化 | **本轮未做导入集成**（交接要求工具通过后另提交）；导入/索引迁移单独任务 |
| OCR 依赖与默认安装隔离 | 依赖在 `requirements-ocr.txt`，应用不导入本工具；离线测试不安装 OCR 依赖 |

## 测试命令与结果

```powershell
# 离线（默认环境，无需 OCR 依赖）
python tests/ocr_preprocess_check.py
# 真实引擎（独立环境安装 requirements-ocr.txt 后）
python tests/ocr_preprocess_live_check.py
# 全量离线回归（含上面两项；live 缺依赖时 SKIP）
python tests/run_checks.py
```

结果：

- 离线回归：**12/12 通过**（文字层保留、扫描页 OCR、混合 PDF、无置信度不伪造、可疑页、
  乱码回退、单页失败、旋转兜底、页码选择/上限、拒绝覆盖与原件不变、损坏 PDF/图片/同名输出、
  CLI `--engine none`）。
- 真实引擎：**30/30 通过**；中文/英文召回 1.000，混合 PDF 分流正确，旋转页与表格保留全部关键词，
  置信度被保留，原件 SHA-256 不变，无失败页。
- 全量 `tests/run_checks.py`：**37/37 通过**（新增两项；live 在装了 OCR 依赖的环境实际执行）。

真实引擎证据（本机，未提交）：`.verify/ocr-live-evidence/`（合成图片、`.ocr.json`/`.ocr.txt`、
`ocr-live-summary.json`）。

## 人工对照原图观察（真实差异，未修正）

- **旋转页**：3 行文字逐行识别正确，但输出行序反转（原图从上到下，OCR 从下到上）。
  字符检索可用，但不能直接当段落原文；接入时需保留方向/顺序警告或人工核对。
- **表格/多栏**：2×2 样例第 1 行被合并成一行、第 2 行被拆开；未做版面重建，行列可能串联；
  数字与单位本身正确。
- 中英文空格可能增减（如「每日上限400元」），字符内容正确。

这些是引擎/版面还原能力边界，不是工具伪造；已记录在文档和输出警告中。

## 未做 / 未测

- 未接入导入界面、未重处理旧文档、未重建索引（按交接要求作为后续独立提交）。
- 未对真实私有文档逐页审计；本轮只使用合成样例；合成样例召回不代表业务准确率。
- 旋转只覆盖 90° 倍数；低质量扫描/透视畸变未评估。
- 未做 Windows 打包/服务化；工具是脚本级本地运行。

## 下一轮（MCP）说明

MCP 小服务单独提交，与本轮文件不重叠；详见 `docs/MCP_SERVER.md` 及其提交说明。
