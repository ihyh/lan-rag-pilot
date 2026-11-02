# 离线答题项摘录核对工具

`scripts/slot_evidence_trial.py`接受固定问题、显式答题项、保存的来源和模型JSON，生成仅包含程序复制原文的核对稿。没有应用路由、默认开关或模型调用。答题项由调用方预先给出，工具不自动拆解问题。

## 机械校验

- 问题、答题项及顺序、来源正文/元数据及顺序共同绑定SHA-256；漂移后拒绝回放。
- 调用方应在发题前保存期待SHA，回放沿用原值；不能改输入后重新计算期待值来证明没有漂移。历史回放需同时核对原始请求/响应哈希。
- 每个答题项必须恰好出现一次；拒绝额外字段、重复JSON键、无效来源编号及非字符串正文，包括null。
- 引文只在模型指定来源中定位，不跨来源修号。允许空白段折叠为单个空格，保留ASCII词/数字边界，不删除所有空白或修复字词、数值、标点。
- 引文重复无法唯一定位或未找到时，该项转待核对。定位成功后程序复制原始整行，保留条件、否定、换行和原文字符偏移。

## 渲染边界

模型的`answer`只作为`untrusted_model_answer`留在JSON审查记录，不写入`rendered_text`。因此额外解释或术语扩译不会进入摘录核对稿。它没有被程序证明为错误或修正成正确答案。

`not_in_sources`只转成`no_evidence_selected`，显示“未选出依据，不能据此认定资料没有答案”。这是防止错误宣称资料缺失的提示，不能补回漏选证据。

`evidence_draft`仅表示所有给定答题项都定位到了引文；不是语义验收通过。`fact_supported`及`whole_answer_supported`保持null，始终需要语义复核。词义、冲突是否召回完整、场景是否匹配及是否遗漏前提，均不能由字面匹配证明。索引片段行号也不是原件页码或物理行号。

JSON结果的`source_locators`保留来源编号、文件名、片段ID及已给出的索引页/段落字段，可与原始输入交叉核对；它不会补造缺失页码。

PDF硬换行可能造成保守拒绝。例如原文`一键\n校准`与模型`一键校准`不会自动合并，需核对解析结果；不能为通过某一文件而放宽所有文档的词边界。

## 调用

```python
import json
from scripts.slot_evidence_trial import render_slot_evidence, slot_snapshot_sha256

question = 'P42用途？'
sources = [{'filename': 'demo.txt', 'content': 'P42用于压力保护。\n'}]
slots = [{'slot_id': 'purpose', 'question': 'P42用途？'}]
raw = json.dumps({'items': [{'slot_id': 'purpose', 'status': 'answered',
    'answer': '待审查的模型解释', 'source_index': 1, 'quote': 'P42用于压力保护。'}]})
snapshot = slot_snapshot_sha256(question, sources, slots)
draft = render_slot_evidence(question, sources, slots, raw, snapshot)
print(draft['rendered_text'])
```

回归入口：`tests/slot_evidence_check.py`，只用合成资料和标准库，不访问模型、私有文档或生产服务。

## 候选编号协议

同模块的`prepare_candidate_trial(question, sources, slots)`为原始段落分配`S1P1`等编号，保存原始字符offset和正文SHA。只按空白行分段，无空白行的逐词换行表格保持完整chunk；不合并换行、不重写字符，也不保证输入chunk已包含完整事件定义。超过200段会停止，不裁剪。

准备结果包含`messages`与`candidate_snapshot_sha256`，调用方必须在发题前保存。模型只可返回：

```json
{"items":[{"slot_id":"purpose","candidate_ids":["S1P1"]}]}
```

`render_candidate_evidence(question, sources, slots, raw, expected_sha)`重建候选表并核对快照，候选编号/offset、来源身份、问题、答题项或协议版本漂移都拒绝。它不接收调用方提供的候选正文/坐标，不接受自由`answer`或`status`，也不修正未知编号。

一个答题项可选择多个候选以保留相关前提或不同来源的冲突。返回结果按来源顺序排列；同ID跨答题项复用只展示一次正文，相同文字来自不同ID/来源仍分别保留。原文直接按原始offset复制，无需再次定位模型抄写的引文。

空编号数组仍只表示未选出依据。编号合法不证明选段相关、前提齐全或冲突召回完整；所有语义支持字段继续为null。本文两种协议都是离线核对工具，均未接入应用问答。
