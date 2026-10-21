# OCR API / 选段恢复评估复核

日期：2026-10-21。基线 `468b720`，候选 `0a1414cc523e7d73ccf1fdef4e4085fb9927469e`（含 A3 修复/API 证据 `f46afad`）。本地与 GitHub 功能分支一致，main 仍为 `abf6d24`。无产品代码改动、提交、推送、合并或部署。

## 结论

**OCR 按钮 A3 已关闭；新增评测器/验收结论需修改。** 发现 E1、E2 两项 P2。它们不否定保存的 12 题证据指标，但阻止把本轮报告作为开启默认功能或“整段回答受支持”的依据。CPU rerank、补证/续段等默认值未被本轮提交改变。

## E1 / P2：新增评测分支与实际 RAG 的空范围、无候选语义不一致

位置：`scripts/retrieval_eval.py:201-205`；对照 `app/routers/query.py::retrieve_sources`。

实际 RAG 在 `vector_index.search` 无命中时立即返回 `[] / no_hits`；新评测器仍执行 `attach_literal_evidence`。当 case 的 `scope=[]` 时，前面索引正确返回空，但传入补证 helper 的空 set 被既有 `_scope_clause` 当成不限定文档，继而从整库添加来源。普通非空范围下的无 hits 也会被评测器补成有答案，而产品路径根本不会执行此步。

独立复现使用实际 `evaluation.main()`/参数解析、合成 DB 与 mock 嵌入：

| 场景 | 评测候选 | 评测最终来源 | 评测完整 | 实际 retrieve_sources |
| --- | --- | --- | --- | --- |
| scope=[] | [] | [1]（范围外文档） | true | [] / no_hits |
| scope=[1]，索引无 hits | [] | [1] | true | [] / no_hits |

第一行用真实 VectorIndex 空范围行为，第二行只将 search 设为无命中；字面补证、评测器和生产检索函数均实际执行。未连接真实业务模型或服务。此为评测越界/虚高，不声称当前生产接口存在同样的全库泄漏。

最小修复：评测器沿用实际 RAG 的无 hits 短路语义，空 scope 永远不得产生来源；补充空 scope、普通无 hits、正常有 hits 的 flag ON/OFF 对照。若未来要允许“无向量候选但字面命中”的产品降级，应另做明确产品变更并让两条路径一致，不能只在评测器实现。

本批保存的 12 题全有候选，且无空 scope；因此这两条边界未污染本批 `3→5→6` 的重算结果，不要求凭空否定该有限结果。

## E2 / P2：把事实 token 命中/存在引用误写成整段答案支持性通过

位置：`docs/OCR_E2E_ACCEPTANCE.md:49-51`、`docs/SELECTION_RECOVERY.md:30-36`，以及所引用的本地答案报告。

保存的 API 第 2 答确实答出 `Unknown Command` 和默认值 2，但随后说“无法确认其是否为重试次数”，又马上引用“Byte3 重试次数默认值为 2”。来源已经明确该角色，回答却增加自相矛盾的无依据不确定性。因此 `answer_has_token=true`、`answer_cites=true`、HTTP200 不能推出 `all_supported=true`；`status=ok` 是接口执行状态，不是人工质量判定。

选段恢复 H01/H02 也有相同验收缺口：

- H01 的来源只有 `2,1,ROBOT_NO_HEX_CHANNEL_ERROR` 等告警原文，没有把 HEX 定义成“六轴”或“硬件通道配置异常”；补证后答案仍加入这些解释。代码标签和双文档区别恢复，不等于整答没有推断。
- H02 的 literal_cont 答案从 `cammera init failed*` 扩写“与机械轴或安全状态无关”，从 `Tool not ready (load/unload request not triggered)` 扩写“属于机械动作状态异常”；原所列来源未支持这些排除/分类结论。
- H04 的“行号18当索引18”已被提交说明如实列为失败，应继续保留。

最小修复是**纠正验收口径和结论，不是本轮临时改写生产生成逻辑**：保留原答案，分列核心事实命中、逐事实来源支持、无依据扩写、自相矛盾/误拒答；把上述答案标为“部分事实正确，整答未通过”，撤销 all_supported 的强结论。后续生成侧处理需按已有顺序单独评审，不能只删坏注释或重新生成一遍覆盖首次输出来过关。

## 已关闭：A3 全 ok OCR 文档按钮

执行实际 common/admin 的 loadDocs，在最小 DOM 中确认：all-ok OCR 按钮 disabled、说明指向 OCR 重导入，且不增加“待核对”；suspect/failed 保持禁用/警告，普通文档仍可重建。开发者保存的 `ui-admin-all-ok.png/.json` 已核对，显示就绪且没有误加 OCR 警告。本轮无新的浏览器会话/真实登录；这是实际渲染函数验证及保存截图核对，不是新浏览器验收。

## 保存证据核对与真实进展

- 12 题的 cases/corpus/app代码/evaluator 身份、配置、初始候选一致；逐题指标经当前评测器重算与报告一致，补证/续段用实际 helper 在只读同语料库重放后也与保存来源一致。各题最终来源数与说明一致。
- 完整证据数 `3/12 → 5/12 → 6/12`；证据召回 `45.119% → 61.786% → 65.952%`；初始候选召回 `79.167%` 不变；来源总数 `36 → 59 → 70`；p95 保存值 `26.6 → 36.5 → 45.2 ms`。本轮核对保存值，不重跑真实嵌入耗时。
- “候选骨架不变，所以不是重新召回”的措辞需改（P3）：字面 helper 实际对整个许可范围做 SQL 额外召回，并能补入初始候选池之外的 chunk。应区分初始候选召回和补证后的最终证据覆盖，不能据初始列表不变否认二次召回。
- 保存 API 的三条 chats/answer/status/model 和 chat_sources，与 `rag-api-report.json` 一致，DB model 为 `qwen3:4b-instruct`；引用映射与对应 chunks 一致。实际服务库在 `.tmp-tests/ui-e2e-api/data/rag.db`，而不是导入原库 `.tmp-tests/ocr-e2e-20261021/rag.db`。说明“同一隔离 DB”应明确为隔离副本，并提供其身份/启动配置。
- 能核对持久化与报告一致，不代表独立重放真实登录/模型调用；保存报告未提供完整请求体、原始模型请求/响应、运行脚本/固定配置。后续验收应保存这些必要信息，不能仅凭 model 字段证明全部调用条件。
- 首次合成源/OCR/导入 SHA 与原页已在前轮核对，本轮未修改或重跑 OCR、真实模型或生产索引。真实私有扫描件仍未验收；需要用户提供少量人工核对素材。

## 独立执行及记录

新证据目录：`data/eval/reports/ocr-api-selection-review-20261021`。

- 11 项相关语法/JS/后端检查通过（含守卫6、字面补证39、续段27）；独立 actual-render 与评测边界诊断也成功。
- 初跑总计 14 命令，13 退出0，保存证据审计因猜错 API DB 位置退出1；该首次日志原样保留。定位实际 data 子目录，并按真实 excerpt 规则归一化空白后，仅重跑此只读审计，成功结果/单独日志保留于 `saved-evidence-recheck.log`、`saved-evidence-recheck-status.json`。不把审核脚本选址错误算成产品回归，也不隐藏该失败。
- `checks.json`：首轮命令、HEAD/文件哈希；`eval-edge-results.json`：E1 两原反例；`saved-evidence-results.json`：重算/重放/API持久化核对；`render-results.json`：A3及相邻UI。审查中 Windows rg 参数误用了 PowerShell 选项，已纠正，未改用户文件。
- 没有运行完整48项回归、没有新浏览器/生产验收、没有新真实生成或模型下载；没有自动开启任何候选功能。审核只写报告/规划和自有隔离证据，未改产品实现。

## 下一步交给 DeepSeek

先用小提交修 E1 并补三类边界回归，同时纠正 E2 的整答验收判据、保留原失败证据并修正文档措辞；交新 SHA 复核后，再按现有顺序推进生成侧最小修复。不要把本轮有限证据提升当成整体回答正确率提升或默认启用依据。
