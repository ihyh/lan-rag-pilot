# RAG 评测集

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

在 Ubuntu 服务器运行：

```bash
cd /home/ihyh/rag-pilot
python3 scripts/eval_runner.py --validate-only
python3 scripts/eval_runner.py --username eval_user --out data/eval/reports/$(date -u +%Y%m%dT%H%M%SZ).json
```

密码会交互式输入，不会写入报告。评测会真实产生问答历史和审计记录，建议使用单独的 `user` 账号，不要使用 root。

默认评测集为 `data/eval/questions.jsonl`，报告写入 `data/eval/reports/`；整个 `data/` 目录已被 Git 忽略。报告 `summary` 会同时给出 `top5_hit_rate`、`citation_location_accuracy`、`refusal_accuracy` 和 `auto_pass_rate`。正式门槛是 Top-5 命中率至少 90%、引用位置正确率至少 95%，答案忠实度仍需人工逐条复核。
