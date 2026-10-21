# 答案验收判据（严格版）

用于生成/检索验收报告，避免把“关键字出现 + 有引用编号 + HTTP 200”写成整段答案受支持。
判据适用于保存的答案原文，不覆盖首轮输出。

## 分列字段

| 字段 | 含义 |
| --- | --- |
| `execution_ok` | HTTP/模型调用是否完成；不代表内容正确 |
| `core_fact_present` | 关键事实 token 是否出现；只说明命中，不说明整答 |
| `citation_indices_present` | 回答中的 `[n]` 编号是否在来源范围内；只说明格式/可解析 |
| `fact_supported` | 逐条事实是否有**对应来源原文**支持（需人工或逐条程序核对） |
| `unsupported_expansion` | 答案加入来源没有的解释/分类/排除/因果 |
| `self_contradiction` | 前句与后句/来源矛盾（例如先说无法确认、随后又引用同一事实） |
| `wrong_fact` | 与来源明确冲突（例如把行号当索引） |
| `false_refusal` | 来源已有答案却答“未提及/无法回答” |
| `whole_answer_supported` | 全部实质事实均有来源且无上述问题；未人工核对时一律 `false` |

## 已保存答案的严格标注

`.verify/ocr-e2e-20261021/answer-quality-review.json`：

- API 第 1/3 答核心事实与来源一致，人工标为通过；
- API 第 2 答核心事实正确，但出现“无法确认是否为重试次数”与随后引用同一“重试次数”
  的自相矛盾，`whole_answer_supported=false`；
- 选段恢复 H01/H02 的核心故障文案正确，但含来源未支持的“六轴/硬件配置”“与机械轴或
  安全状态无关/机械动作状态异常”等扩写，整答未通过；
- H04 把行号 18 当索引 18（应为 20），`wrong_fact=true`，整答未通过。

## 规则

- 不允许用 `core_fact_present` 或 `citation_indices_present` 推出整答通过；
- 不允许为了“过验收”重写首轮答案或只删坏注释；失败样本原样保留；
- 只有 `whole_answer_supported=true` 才能写“该题回答正确”；小样本只写“该题”，不写整体正确率。
