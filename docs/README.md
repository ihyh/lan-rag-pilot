# 文档索引

当前功能与进度以 [README](../README.md) 和 [ROADMAP](../ROADMAP.md) 为准。正式前端为原生 JavaScript；默认部署面向 Windows 本机与受控私网。

## 使用与运维

- [首次使用](GETTING_STARTED.md)、[Windows 安装](WINDOWS.md)、[模型配置](MODELS.md)
- [备份与更新](OPERATIONS.md)、[安全边界](SECURITY.md)、[故障排查](TROUBLESHOOTING.md)、[发布验证](RELEASE_GATE.md)
- [版本与适用范围](DOCUMENT_VERSIONS.md)、[OCR 上传](OCR_UPLOAD.md)、[OCR 预处理](OCR_PREPROCESS.md)、[OCR 入库](OCR_RAG_IMPORT.md)
- [查资料与纠错](QUALITY_WORKBENCH.md)、[反馈审核](FEEDBACK_REVIEW.md)、[来源定位](SOURCE_EVIDENCE.md)
- [检索验证](RETRIEVAL_VALIDATION.md)、[评测工具](../eval/README.md)、[业务评测流程](../eval/BUSINESS_EVAL_WORKFLOW.md)

网页/日志导入已移除，旧记录兼容见 [网页来源](WEB_IMPORT.md)、[日志来源](LOG_IMPORT.md)。MCP 服务和反馈导出界面不再提供。没有自动备份调度脚本；按运维文档备份。

## 实验与设计记录

[通用检索](GENERIC_RETRIEVAL.md)、[重排评估](RERANK_EVALUATION.md)、[证据补全](EVIDENCE_COMPLETION.md)、[上下文续接](CONTEXT_CONTINUATION.md)、[表格答案](TABLE_ANSWERS.md)、[参数答案](PARAMETER_ANSWERS.md)、[隔离复测](ISOLATED_REPLAY.md) 描述开关、工具或开发方法，不能全部视为默认启用能力。具体默认值以 `app/config.py` 为准，实例配置可能不同。

其余专题文档包含阶段性试验；文中私有报告、本机路径或未随仓库提供的脚本，不是克隆后可直接执行的步骤。公开回归入口为 `tests/run_checks.py`，前端交互测试在 `tests/ui`，端到端脚本为 `tests/smoke_runner.ps1`。

## 历史记录

旧部署和验收记录放在 [_history](_history/README.md)；远端早期评审保留在 `docs/reviews/`。日期、通过数和本机报告只代表当时的过程，不代表当前版本验证或现有功能。本机资料、模型、数据库及预览文件不会随源码发布。
