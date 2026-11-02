# 网页图片与截图OCR来源准备

当前状态（2026-10-27 15:22）：56张网页图片已全部入库并设为所有登录账号可见。3张沿用已有人工确认结果；53张文字已入库但仍待人工核对，其中9张OCR标为可疑。图片和图表关系、阅读顺序均保留未验证提示；单图核对入口仍可使用。原件和OCR暂存于安装目录`data/web-images`，详见本批报告（未随仓库提供：`../data/eval/reports/web-image-batch-20261027/REPORT.md`）。手机测试为`SKIPPED_BY_USER`。下方是历史隔离准备记录。


2026-10-23：`scripts/prepare_image_sources.py` 在全新隔离目录保存原图、OCR结果及每次来源引用，复用已有CPU RapidOCR环境。它不写RAG数据库，不改变生产上传接口。

## 使用与产物

用已有独立OCR解释器运行：

```powershell
.venv-ocr\Scripts\python.exe scripts\prepare_image_sources.py --snapshot-dir ..\.tmp-tests\web-import-new-run --output-dir ..\.tmp-tests\image-run-new
.venv-ocr\Scripts\python.exe scripts\prepare_image_sources.py --image screenshot.png --image screenshot.jpg --output-dir ..\.tmp-tests\screenshots-new
```

输出目录必须位于工作区外层 `.tmp-tests` 且尚不存在。网页输入必须是前一阶段四篇文章的已保存快照，栏目成员/原始HTML SHA/图片DOM序列须一致；只下载当前已确认的 `img2022.cnblogs.com` HTTPS PNG来源。独立图片支持实际PNG/JPEG，每图≤10MB、≤2000万像素；多帧、坏格式和来源重定向拒绝，不执行网页脚本。

- `originals/`：原图字节与SHA，未转PDF或修改原图。
- `ocr/`：识别结果原样保留，含文字、行分数、文字框、警告及旋转角度；空文字不能作为可用OCR。
- `references.json`：每次图片引用的文章URL/标题/HTML SHA/采集时间、正文DOM路径、图序、实际上级章节；独立截图保留路径/文件名/SHA。
- `progress.jsonl`、`manifest.json`：逐项状态和中断/失败可见。同图可复用OCR计算，每次文章/位置归属独立，失败资产也保留原件与最初错误。
- `review.html`：本地静态核对稿，展开查看原图和OCR文字。不是管理页，也没有会伪称“已审核”的确认按钮；真实浏览器样式/操作尚待验收。

## 准确性边界

`ocr_preprocess.py` 现在可选保存 `lines`，包括text、score及中心/宽高；未知值为null。`original_image_size`为未旋转识别输入的尺寸；`ocr_image_size`为旋转后的尺寸，框坐标属于`rotated_ocr_image_pixels`，不能直接叠在未旋转原图上。PDF文字层不强行补造OCR几何。OCR内部单图frame1不是PDF物理第1页。

所有图片均为`index_eligible=false`，非空成功/可疑文字仍`review_state=required`，`visual_relations_verified=false`。图中箭头、表格行列、流程关系和多行标签顺序不能由OCR标签顺序证明；引擎ok/分数高也不代表文字或含义已人工通过。

## 本轮验证

- 指定四篇的56个图片引用全部下载并完成OCR：46个引擎ok、10个suspect；均未人工审核、未索引。
- 56条引用逐一重读核对文章/DOM、原图SHA、OCR来源SHA/结果SHA、尺寸和1606条文字框，来源链一致。
- PNG/JPEG合成截图恢复已知400、18–27；90°旋转图以270°OCR转正，原图不变；空白图failed。真实解码负例覆盖空/损坏/超字节/GIF/多帧/超像素。
- 原始虚线被识别为1/I、多行标签顺序颠倒等真实失败保留。可疑结果不自动纠正或发布，不能将本批计为问答准确率。
- 合成检查覆盖跨文章同图/同文重复引用、下载失败、可疑/空文字、错误来源SHA、同图失败缓存及未知几何。新字段保留旧OCR质量逻辑。
- 全套离线79/79通过；独立代码复审无剩余P1/P2。最终工具对56份保存结果完整回放一致，未重新下载或识别，不能把回放算成第二轮独立OCR确认。

尚待完成：人工逐项核对或纠正、正式图片上传/审核/索引与检索引用展示、图表关系处理，以及真实私有截图和端到端问答验收。程序没有自动审核业务金标；未引入云端识图或新模型下载。

2026-10-24后续：独立PNG/JPEG的管理页上传、原图核对、手动确认入库和“原图”来源标记已开发，见[OCR_UPLOAD.md](OCR_UPLOAD.md)，尚未部署。本文56张网页图片仍是未审核隔离准备结果；不能把新增上传能力视为它们已经被人审或已进入生产库。
