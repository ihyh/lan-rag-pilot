# 隔离原文摘录候选（未接入在线问答）

目标是验证：让模型只选择来源行号，程序从冻结的来源中逐字复制，避免自由生成把数字、URL或参数名抄坏。它不是已经验收的问答模式，不会修补旧答案，不证明选段支持问题。

实现：`scripts/source_quote_trial.py`，只用标准库，无数据库、网络、模型调用或在线配置。调用方负责提供完整来源和模型原始JSON；当前仅用于有限、固定、小样本隔离诊断，不是无限制上传或在线接口。

## 整块保留候选（修复已选块内部丢限定）

新增 `build_record_messages` / `render_source_records`，复用下文的快照和逐字渲染，只允许模型提交：

```json
{"source_indices":[1,2]}
```

程序自行生成每块第1行到末行的范围；模型不能裁行、改值或增加正文。这样同一已选块中的默认/示例/当前值标签、否定和备注不会再被选行删掉。它**不自动识别字段角色**，也不证明挑对来源；跨块条件、漏冲突、原始索引已残缺和单位换算仍需另验。`CurrentDensity`不会因为含Current就被误分类。

调用顺序仍是深拷贝来源 → 请求前计算快照 → `build_record_messages` → 保存原始选择 → `render_source_records(..., snapshot)`，不得响应后重算预期哈希。

最多20个来源、合计12000个来源正文Unicode字符，超限明确停止，不截断块；预算不是完整元数据/问题/token预算，因此仍仅用于有限小样本离线试验。空选择不证明缺证，选空字符串块报错；纯空白块仍可能被物理复制，不代表有用证据。`selected_blocks_complete`只说明已选**检索块**完整，不等于完整业务记录/文档或整答正确。

也应保留零模型基线：用所有来源编号调用同一渲染器，完整呈现当前有界输入证据。它不漏掉输入里的跨块条件/冲突，但可能带入无关来源，仍不是自动答案。如果模型挑源没有可验证收益，不增加额外模型调用。

2026-10-23本轮19项纯检查及旧已消费失败回归已验证同块限定保留；6组新来源完成零模型整块核对，模型对照已封存但按用户要求暂缓。**没有新的模型选择成绩或准确率结论，也没有线上接线。**

上述暂缓已由用户后续“现在执行封存对照”恢复：19:15完成原6题/12次请求，无重试或改题。整块候选恢复4个场景的关键遗漏、1个冲突场景与旧方式等效，换算场景仍仅摘录。零模型基线同样保留目标证据但冗余更多；不称6/6答案正确，仍没有线上接线，详见模型对照复核（未随仓库提供：`reviews/2026-10-23-source-record-model-trial.md`）。原暂缓与前置快照保留为历史。

2026-10-23后续已完成真实26文档副本的4题/12次选证与同生成器回答对照。选证保留目标证据，但表格Value角色误读仍存在；另外真实开发分派已由table_answers直接正确呈现该表格题，0模型调用。暂不接在线挑源，优先真实链路验收已有确定性分支。零模型仅指不挑源，非不生成答案；详情见真实资料对照（未随仓库提供：`reviews/2026-10-23-real-record-comparison.md`）。

## 旧自由裁行调用（仅保留作对照）

```python
from copy import deepcopy
from scripts.source_quote_trial import (
    build_quote_messages, render_source_quotes, source_snapshot_sha256,
)

frozen_sources = deepcopy(sources)
snapshot = source_snapshot_sha256(frozen_sources)  # 请求前冻结身份
messages = build_quote_messages(question, frozen_sources)
# 隔离调用模型，保存实际messages和raw_selection，不重试择优。
result = render_source_quotes(frozen_sources, raw_selection, snapshot)
```

模型仅输出如下JSON，不允许答案正文、数值补写、Markdown包裹或额外属性：

```json
{"quotes":[{"source_index":1,"start_line":1,"end_line":2}]}
```

来源编号从1起，行号从1起且首尾包含。来源数组顺序、元数据和全文共同参与快照哈希。响应回来后不能重新计算一个新“预期哈希”来掩盖来源变化；要保留请求前那个哈希和深拷贝快照。

拒绝重复JSON键、非整数/布尔坐标、额外属性、越界、反向范围及来源漂移，不裁剪/修复选择，不回退自由生成。空选择只表示“未选出摘录”，不证明资料不足。所有原始失败应保留，不覆盖后重试凑通过。

## 输出与限制

- `quotes[].text` 直接切原文完整行，保留大小写、符号、精度、空白、CRLF和末行无换行；不替换字符、不补URL、不换算单位。
- 每段保留来源编号/正文哈希、行号和Unicode codepoint偏移（从0起、右端不含）。`excerpt_text` 用动态代码围栏展示原文，包装字符不属于 `quotes[].text`。
- `source_fully_quoted` 只表示复制了该**检索片段**全部行，不证明整份文件、定义或条件完整。`unselected_source_indices` 显示未选来源，不能静默把它们当作无关或错误。
- 摘录可能选错默认/当前值，漏掉上一行条件，或只选冲突中的一条。`fact_supported`、`whole_answer_supported` 始终未知，`human_verified=false`，`manual_review_required=true`。
- 换算、综合解释等问题仅摘录不足以回答；模型选择正确和程序复制正确也必须分开验收。不要把来源引用合法、HTTP200或摘录可渲染算整答通过。

工程检查：`tests/source_quote_check.py`。本机4B对照必须保存原始请求/响应、固定模型/参数和来源，明确它绕过真实检索及应用分派，不冒充生产端到端RAG成绩或人工业务金标。只有新样本证明字段/条件/冲突处理可用后，才讨论共享chat/stream入口的明确摘录模式；本轮没有该接线或开关。
