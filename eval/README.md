# RAG 评测集

[返回首页](../README.md) · [模型选择与对比](../docs/MODELS.md)

这里存放由业务人员根据真实知识库文档填写的问题，不自动生成标准答案。

每行一个 JSON 对象，字段示例：

```json
{"id":"q001","question":"文档中的真实问题","expected_answer":"人工核对过的标准答案","expected_sources":[{"filename":"真实文件名.pdf","page":3}],"answer_keywords":["标准答案中的关键短语"],"should_refuse":false}
```

规则：

- 至少填写 30 条真实问题；`expected_answer` 和 `expected_sources` 必须人工核对。
- PDF 优先填写 `page`；TXT/MD/DOCX 可填写 `paragraph` 或 `chunk_id`。
- 无答案问题设置 `should_refuse: true`，并将 `expected_sources` 设为空数组。
- `answer_keywords` 只用于粗略自动检查，答案最终仍需人工复核。
- 不要把密码、API Key 或完整内部原文写入评测文件。

在受控 Ubuntu 验收环境运行（源码部署目录示例 /opt/rag，Python3 已离线安装）。先在该实例建立专用 eval_user 普通账号，将下列域名替换为实际内部 HTTPS 入口，系统必须信任内部 CA：

```bash
cd /opt/rag
python3 scripts/eval_runner.py --validate-only
python3 scripts/eval_runner.py --base-url https://rag.intra.example --username eval_user --out data/eval/reports/$(date -u +%Y%m%dT%H%M%SZ).json
```

密码会交互式输入，不会写入报告。评测会真实产生问答历史和审计记录，建议使用单独的 `user` 账号，不要使用 root。

默认评测集为 `data/eval/questions.jsonl`，报告写入 `data/eval/reports/`，路径相对运行目录；这里指宿主机上的评测文件，不是自动从容器数据卷读取。整个 `data/` 已被 Git 忽略。报告含问题与结果，属于受控数据，不上传公网。

报告 `summary` 给出 `top5_hit_rate`、`citation_location_accuracy`、`refusal_accuracy` 和 `auto_pass_rate`。Top-5 至少 90%、引用位置至少 95% 可作为待业务确认的验收目标，**不是已经达到的结果或通用保证**。记录实际 Top-K；指标名称不会自动让系统固定为 5。答案忠实度须人工逐条复核。

Windows 可在独立验收副本用 `.\.venv\Scripts\python.exe` 运行同一脚本；个人 HTTP 回环实例用 `--base-url http://127.0.0.1:8088`，不要用 HTTP 测试要求 Secure Cookie 的 LAN 实例。正式运行仍禁止外网。

脚本会连续调用查询接口；默认每分钟限流可能造成 429。按验收计划在独立实例安排请求速率或调整测试实例设置，不关闭生产限流。保留错误记录，不能将失败题排除后宣称通过。
