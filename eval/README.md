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
- 可回答问题必须至少填写一个 `expected_sources`；无答案问题设置 `should_refuse: true`，并将 `expected_sources` 设为空数组。矛盾标注会在运行前失败，不能进入指标。
- `answer_keywords` 只用于粗略自动检查。未填写关键词的回答内容会标为 `manual`，不会计入 `auto_pass`；拒答是否命中另看 `refusal_match`，不能代替回答文本复核。答案最终仍需人工逐条确认。
- 不要把密码、API Key 或完整内部原文写入评测文件。

在受控 Ubuntu 验收环境运行（源码部署目录示例 /opt/rag，Python3 已离线安装）。先在该实例建立专用 eval_user 普通账号，将下列域名替换为实际内部 HTTPS 入口，系统必须信任内部 CA：

```bash
cd /opt/rag
python3 scripts/eval_runner.py --validate-only
python3 scripts/eval_runner.py --base-url https://rag.intra.example --username eval_user --out data/eval/reports/$(date -u +%Y%m%dT%H%M%SZ).json
```

密码会交互式输入，不会写入报告。评测会真实产生问答历史和审计记录，建议使用单独的 `user` 账号，不要使用 root。

默认评测集为 `data/eval/questions.jsonl`，报告写入 `data/eval/reports/`，路径相对运行目录；这里指宿主机上的评测文件，不是自动从容器数据卷读取。整个 `data/` 已被 Git 忽略。报告含问题与结果，属于受控数据，不上传公网。

报告 `summary` 给出文档级 `document_hit_rate`、位置级 `citation_location_accuracy`、`refusal_accuracy`、`auto_pass_rate` 和本次响应实际出现过的 `observed_max_sources`。查询接口不回传运行时 Top-K，因此报告不再把文档命中率命名为 Top-5；`observed_max_sources` 也只是观测上限，不能冒充配置值。文档命中至少 90%、引用位置至少 95% 可作为待业务确认的验收目标，**不是已经达到的结果或通用保证**。答案忠实度须人工逐条复核。

Windows 可在独立验收副本用 `.\.venv\Scripts\python.exe` 运行同一脚本；个人 HTTP 回环实例用 `--base-url http://127.0.0.1:8088`，不要用 HTTP 测试要求 Secure Cookie 的 LAN 实例。正式运行仍禁止外网。

脚本默认保证相邻查询至少间隔 6.1 秒，对应服务默认每分钟 10 次的限流；模型调用本身超过该时长时不会额外等待。若独立验收实例使用不同限流，可通过 `--min-interval` 调整，只有确认实例不限流时才设为 0。不要关闭生产限流；保留 429 等错误记录，不能将失败题排除后宣称通过。
