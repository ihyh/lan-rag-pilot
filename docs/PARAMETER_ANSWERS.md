# 命令参数列表（Byte N）：确定性回答（实验，默认关闭）

## 为什么

真实复核明确指出：H05 虽然把 Byte1/2/3 的原文都补进了上下文，模型回答仍把
“Byte3 消息重试次数”概括成时间单位，或补出原文没有的“count/单位”。
这类题的关系在原文中已经写死：

```
Byte 1: Inter-Character time-out in tenths of seconds (must be >4: ... default value of 40).
Byte 2: ENQ-EOT time-out in tenths of seconds (must be >19: ... default value of 200).
Byte 3: Message retry count (must be >1: ... default value of 2).
```

本模块只对“问题点名 Byte N、且原文有对应块”的题做服务端拼装回答，其余问题仍走模型。

## 开关与默认

| 环境变量 | 默认 | 行为 |
| --- | --- | --- |
| `RAG_PARAMETER_ANSWERS` | `0` | Byte 参数列表题按原文块确定性回答；默认关闭，需显式开启并做前后对照 |

开启只改变这一类题的生成方式；不改检索、chunk、向量、Top-K、权限或引用编号语义。
与 `RAG_TABLE_ANSWERS`、`RAG_CONTEXT_CONTINUATION`、`RAG_EVIDENCE_COMPLETION` 相互独立。

## 命中条件

1. 问题点名 `Byte N`（只取实际出现的编号；片段没有的编号明确写“未提供”）；
2. 问题问到控制/功能、限制、默认值、计量单位之一；
3. 检索片段中有 `Byte N: ...` 块（PDF 折行会合并；`Parameters: Byte 1:` 前缀也能识别）。

命中后逐项输出：

- 控制内容：从原文块提取（不含单位短语）；
- 限制：只输出原文 `must be >N`；
- 默认值：只输出原文 `default … N`；
- 单位：只输出原文 `in tenths of seconds` 等写法；原文没有单位就写“未给出计量单位”，
  对重试次数明确写“该项是重试次数，不是时间单位”，不补 `count/次`；
- 每项标注真实来源编号 `[n]`。

不命中（未点名 Byte、无属性词、没有对应 Byte 块）返回 `None`，交回原生成路径；
不调用模型、不推测。

## 边界

- 只解析 `Byte N: ...` 形态；其他参数命名（REG/字段）不在此路径；
- 单位只认原文明确写法，不做翻译或换算；
- 不改变检索结果与引用编号；关闭开关时行为完全不变。

## 验证

- `tests/parameter_answers_check.py` —— **18 项通过**：Byte1/2/3 控制/限制/默认/单位、
  Byte3 不继承时间单位、片段缺 Byte 不接管、无属性词不接管、`llm.chat` 开关开启不调用模型。
- 真实固定证据对照（私有，不提交仓库）：H05 由“单位均为时间单位”的模型回答变为
  Byte1/2、Byte2/19/200、Byte3 重试次数 1/2 且“未给出计量单位”的确定性回答；
  其余题不触发，继续走原生成。
- `tests/run_checks.py` 全量回归通过；开关默认 `0`，未开启、未部署。

## 下一步

- 用更多真实参数题验证命中/回退比例，再决定是否默认开启；
- 步骤/条件/否定类问题仍需要生成侧改造，不用本模块硬编码；
- 不扩大成通用解析引擎，也不重建生产索引。
