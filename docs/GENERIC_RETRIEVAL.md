# 通用检索实验（默认关闭）

本轮只增加可独立对照的检索候选策略，不修改生成模型、生成提示、解析、切块、向量、chunk ID 或默认 Top-K。不恢复 CEID 目录或固定答案。代码更新不等于生产部署；目前没有可用于宣称业务正确率的人工验收集。

## 开关

| 环境变量 | 默认 | 行为 |
| --- | --- | --- |
| `RAG_GENERAL_LEXICAL` | `0` | 在现有技术词与语义通道之外，增加中文二字片段、单字查询回退和完整数字的 OR/BM25 候选，再按排名 RRF 融合 |
| `RAG_RERANK_ENABLED` | `0` | 用本地 `BAAI/bge-reranker-v2-m3` 在 CPU 上重排融合候选；batch=4，模型窗口512 token |
| `RAG_RERANK_MODEL` | `models/bge-reranker-v2-m3` | 本地模型目录；不会在服务启动时自动下载 |
| `RAG_HISTORY_AUXILIARY` | `0` | 当前问题独立语义检索，加上一轮用户问题的辅助通道，先2:1 RRF，再与词法通道融合一次 |

重排关闭时仍走原选段路径；历史开关关闭时保留原“上一问＋当前问”的基线路径。仅当前问题的语义对照通过离线评测的 `--history-mode current` 运行，不新增另一套生产开关。词法、编号和显式文件名始终取当前问题，不读取历史回答。所有通道先应用账号/会话文档范围及显式文件范围，再排名截断。

默认 Top-K=3 时，融合候选上限仍为60，最终来源最多3条。完整数字只提供候选，不证明字段角色相同，也不固定置顶或列尽全库同号条目。不同文档、位置、数值和条件不按相似文字去重；重排前只去掉同文档同位置的完全相同文本。重排分数单独记录，不改变 API 原有 `score` 的余弦含义。

长来源按窗口评分，最高窗口分数决定该来源的次序，生成侧仍接收完整原始片段。启用重排后保留可验证的同文档续段，不用旧相关性启发式覆盖模型排序。模型缺失/加载失败时 `/api/ready` 返回503；有候选的问答在重排失败时返回 `rerank_unavailable`，不静默回退。空库、无权限或无候选仍按既有路径处理。

## 准备模型

只下载官方公开模型，不上传文档。使用已经安装项目完整依赖的虚拟环境，在有约2.3GB下载空间和足够运行内存的独立目录执行：

```powershell
$env:HF_HUB_OFFLINE = '0'
.\.venv\Scripts\python.exe scripts/install_reranker.py --destination D:\rag-test-models\bge-reranker-v2-m3
$env:HF_HUB_OFFLINE = '1'
```

工具冻结官方 revision，保存下载标记和 `RERANKER_SOURCE.json` 文件哈希；重复执行验证已准备模型，断点续传沿用原 revision。不要使用含未知文件的目录。网络失败保留现场，不能算模型准备成功。CPU 峰值内存和耗时需以本机真实评测为准；mock 测试没有加载这个模型。

## 分层离线评测

先冻结实际工作区（含未提交改动）、配置、模型身份和 SQLite 一致性备份，不直接复制正在运行的单个主数据库文件。私有题目和原文仅写入本机隔离目录，不能提交仓库。题目格式为 JSON 数组：

```json
[
  {
    "id": "example-dev", "group": "设备甲事实族", "split": "development",
    "question": "检查周期及执行条件是什么？", "scope": [1],
    "required_evidence": [
      {"chunk_ids": [10], "quotes": ["每周", "仅在停止状态"]},
      {"chunk_ids": [11, 12], "quotes": ["禁止带电"]}
    ],
    "index_evidence_complete": true
  }
]
```

每个 `required_evidence` 是一个必要证据单元，其 `chunk_ids` 是可替代来源；多单元必须全部具备，且选段包含对应原文 `quotes` 才算完整。不能用命中任一来源代替多证据完整。无答案题用空数组，保留总体分母，答案是否合理拒答另行评审。索引上游缺失设 `index_evidence_complete=false`，并用 `upstream_failures` 记录 `parse_text_lost` / `chunk_relation_broken`；不能通过排除这类题美化总分。

先冻结40开发/20验收题，按文档或事实族隔离，同一事实不能跨组。已有封存题不默认打开。下面命令不会调用生成模型或修改文档库：

```powershell
$env:RAG_EMBED_MODEL = 'D:\rag-test-models\bge-small-zh-v1.5'
$env:RAG_EMBED_DEVICE = 'cpu'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:RAG_GENERAL_LEXICAL = '0'
$env:RAG_RERANK_ENABLED = '0'
.\.venv\Scripts\python.exe scripts/retrieval_eval.py --db D:\rag-eval\corpus.db --cases D:\rag-eval\cases.json --split development --out D:\rag-eval\baseline.json
```

分别测试词法、重排、两者组合；历史另测 `--history-mode legacy/current/auxiliary`，不可同时改变其他变量。重排实验须显式设置 `RAG_RERANK_ENABLED=1` 及已验证的模型路径。最终20题要等候选和代码冻结后再跑。

报告记录各通道排名、融合、完整候选与选段、取舍原因、重排窗口分数、分段耗时、索引重载耗时及进程峰值内存。报告带代码/工具/题目/语料哈希。旁边的 `.jsonl` 在逐题完成时落盘；中断后换新输出路径，保留未完成现场，不只重跑失败题。总体与“索引已有必要证据”的子集分开报告；未知题保留分母。`fact_correct`、`citation_supported` 为 `null`，表示检索工具没有评定答案真伪。

## 采用、部署与回滚

仅通过公开测试不能开启生产开关。必须通过权限、数字边界、显式范围、同步/流式及旧引用回归；最终20题净增至少2题证据完整，候选召回不下降，且无新的必要证据丢失。再使用同一本地生成模型，对基线/候选各保留两次完整首答，单独评审事实、引用和误拒答。不挑最好的一次，不以更多拒答换改善。合成题成绩不是业务正确率。

任何失败候选保留结果、保持关闭。证据齐全但回答错误属于生成/引用待办；原文或结构已从索引丢失属于解析/切块待办。本轮不靠新增答案特例解决。

获得单独部署授权后才更新运行目录。环境变量修改需重启该独立实例；启动会从已有 chunk 重载内存 FTS，不重新解析文档、不重算向量、不改变旧引用。回滚只需将三个新开关全部设为0并重启；如需代码回滚，恢复先前备份的应用文件及配置。不要删除数据库或对生产执行“重新索引”。
