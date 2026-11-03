# 逐事实原文绑定：离线候选

> 历史开发专题：下文的日期、验证数量、候选状态和本机报告仅描述当时阶段，不是当前安装步骤。MCP 已移除；未随仓库提供的脚本不可直接运行。现有功能与默认边界以 [开发进度](../ROADMAP.md) 为准。

本候选针对“句子事实正确，但把用途、默认值或前提引用到另一片段”的问题。当前v2模型输出每条文字及完整来源编号，程序复用既有快照与完整块摘录器，为每条文字附对应编号和逐字原文。不自动修改旧答案，也不按编号有效判事实正确。

2026-10-25两种协议均已完成本机4B诊断。v1让模型选行，出现遗漏和越界；v2改为逐条文字选完整块，消除了本批裁行问题，但C03用途仍引用错源。**C03尚未修复，候选未接入线上。**三个合成诊断在v2冻结来源内支持完整，不是业务正确率。详见本轮结果（未随仓库提供：`reviews/2026-10-25-claim-evidence.md`）。

```python
from copy import deepcopy
from scripts.source_quote_trial import source_snapshot_sha256
from scripts.claim_evidence_trial import build_claim_messages, render_claim_evidence

frozen = deepcopy(sources)
snapshot = source_snapshot_sha256(frozen)
messages = build_claim_messages(question, frozen)
# 独立本机试验：保存messages和模型原始输出，不重试择优。
draft = render_claim_evidence(frozen, raw_model_output, snapshot)
```

v2模型只能返回这一结构，正文不自带来源编号；协议数据的方括号数字用反引号包裹：

```json
{"claims":[{"text":"一条结论","source_indices":[1]}]}
```

每条文字必须有来源，所有字段先完整校验再返回渲染。非法JSON、重复键、额外字段、空文字/空来源、非整数或越界/重复编号、来源漂移或模型自行添加的引用编号都会报错；旧v1行坐标结构也拒绝，不自动扩行救场。不自动修号、裁剪或回退自由生成。`claims:[]`是合法“未选出”，不意味着整份文档或全库没有答案。

来源沿用原有20块/12,000正文字符预算，不截断原文；每次最多20条文字，每条最多2,000字符及20个来源编号。每条所配证据完整复制对应检索块，从0到正文末尾；这不证明入库块已经包含整份定义。原文中的精度、否定、单位、CRLF和协议括号保持不变。返回的行号只表示检索片段内部行号，不是原件页内物理行号。未选择的来源编号仍保留，不能据此说它们无关。

这条路径只保证**所展示的引文确实来自所配来源块**。如果模型选择合法但不支持该结论的原文，程序仍无法判定语义；`fact_supported`和`whole_answer_supported`保持未知，`manual_review_required=true`。即使模型合并了两个属性、漏掉另一块条件、误读默认/现场或只选冲突中的一条，也必须独立逐项复核，不能把JSON有效算验收通过。C03实际结果正是这个反例，不能因全文中其他块包含用途便放行错误引用。

本候选没有接入Web同步/流式、历史消息或MCP，也没有新增生产API、配置开关或部署。接入前还需评估输出冗余、拒绝后的用户体验及这些调用路径的一致性。工程检查：`tests/claim_evidence_check.py`与原有`tests/source_quote_check.py`。本机模型对照另行封存并保留原始失败；已见回归题和合成诊断题不代表独立业务正确率。
