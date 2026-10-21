# 复核修复说明：续段与字面证据补全（R1–R7）

日期：2026-10-21
复核基线：`587f9b2` + `a0a8763`（功能分支 `feat/manual-document-scope-20261020`）
复核原文：`docs/reviews/2026-10-21-context-evidence-codex-review.md`
本修复提交：见本文件所在提交；两个开关仍默认关闭。

## R1 / P1：补证来源无法持久化

- `chat_sources.score` 由 `REAL NOT NULL` 改为可空；`app/db.py` 新增可重复执行的
  `_ensure_chat_source_score_nullable`，旧库通过重建表迁移，历史行分数/摘录原样保留；
  新库/旧库都允许写入 `NULL`（不伪造相似度）。
- `_store_chat` 改为整轮事务：`BEGIN IMMEDIATE … COMMIT`，任何来源写入失败
  （约束/FK 等）整体回滚，不再留下 `status=ok` 但引用残缺的 chats/会话/来源记录。
- 新增 `tests/context_evidence_persistence_check.py`（20 项）：
  - 新库 score 可空；旧库 `NOT NULL` 迁移保留历史行并可写 NULL；
  - 只开续段/只开字面补证时，普通问答返回来源数 = DB `chat_sources` 行数，
    补证来源 `score IS NULL`，`_sources_for_chat` 历史来源可完整恢复；
  - 只开续段时流式问答正常 `done`、无 error、来源完整；
  - 保存失败（无效 document_id）后 chats 不增加、无来源残行、无新建会话。
- 该测试用真实 SQLite + 真实 `_store_chat` 事务 + 真实普通/流式路由，不只是 MCP。

## R2 / P2：标识符边界与 LIKE 元字符

- SQL 粗召回改为转义后 `LIKE … ESCAPE '\'`（`%`/`_`/`\` 按字面）。
- 召回后用按类型构造的**带边界正则**复核：
  `2,1` 不匹配 `12,10`；`bit4` 不匹配 `bit40`；引号内 `A_B` 不匹配 `AXB`；
  多词字段名要求词边界；准确命中保留正控制。
- 测试：`tests/evidence_completion_check.py` 新增 4 项边界/正控制。

## R3 / P2：固定 40 条截断漏掉后导入文档

- 改为**按文档分批召回**：先 `SELECT DISTINCT document_id`，再对每个文档取有限候选；
  选段时第一轮每文档只取一段（跨文档定义都进入），第二轮才补同文档片段。
- 测试：文档 A 50 条重复代码、文档 B 第 51 条另一解释；B 仍被补入，
  总数仍受 `MAX_EXTRA_SOURCES` 约束。

## R4 / P2：用文件首段冒充目标工作表表头

- 删除无条件 `seq=0` 回退；从目标行内容解析 `工作表《Sheet》第 N 行`，按 **sheet 名**
  查找该工作表 `第 1 行` 所在 chunk，并正则复核后才标记 `table_header`；
  找不到可靠表头时不附加、不标记。
- `query.py` 的表头元数据补挂移到所有组装之后，新增 xlsx 来源也走同一规则。
- 测试：封面先于 Parameters、目标行在 Parameters；断言附加 Parameters 表头而非封面；
  无可靠表头时无 `table_header`。

## R5 / P2：DOCX 的 seq 邻接不是原文邻接

- 续段只处理 `.pdf/.txt/.md`；**DOCX 不再按 seq+1 扩**（现有解析先段落后表格，
  顺序不可靠）。测试覆盖 DOCX 不扩段；文档写明原因与边界。

## R6 / R7 / P2：章节与续文边界

- `CONTINUATION_START` 删除“数字+点/顿号”与泛化的 `xx:` 模式，只保留明确续写标记
  （Byte N/bitN/Pnnn/第N/步骤/续/注：/以及…）。
- `NEW_SECTION` 识别顶层与多级数字标题、中文章节、Markdown 标题；下一段开头即新章节
  → 不附加。
- 新章节出现在续文之后 → 只截取新章节前的续文（`trimmed_at_section=True`），
  Byte2/3 等必要续文保留，`5.4 Reset command` 等无关新章不进入内容。
- 测试：顶层 `6.`、多级 `6.1`、仅 `2、` 均不当续文；续文后新章裁剪保留必要续文；
  既有 22 项续段检查全部通过（共 23 项）。

## 准确率表述更正

- `docs/CONTEXT_CONTINUATION.md` 不再写 H05 “单位均完整”：H05 来源覆盖仍为 1/2→2/2，
  但候选首答仍把 Byte3 重试次数概括成时间单位，**证据补齐不等于答案正确**；
- `docs/EVIDENCE_COMPLETION.md` 保留“覆盖提升 ≠ 准确率提升”，并更新修复后 A/B 数字：
  H01 1/2→2/2、H02 1/2→2/2、H03 1/3→2/3、H04 0/2→2/2、H12 0/2→0/2；
  25 条真实使用 U16 0/3→1/3、0 条回退；
- 私有对照脚本 `compare_continuation.py` 已改为**必须显式传入旧/新结果文件**（不再按 mtime 猜“最新两份”）；
  `run_diagnosis.py` 现在把 Git HEAD、模型、温度与四个候选开关写入每条结果，并输出
  `<结果文件>.config.json` 运行配置，便于固定实验身份。

## 回归

- `tests/context_continuation_check.py`：**23/23**
- `tests/evidence_completion_check.py`：**32/32**
- `tests/context_evidence_persistence_check.py`：**20/20**
- `tests/run_checks.py`：全量通过（含上述新增检查；旧 tokenizer 子项跳过情况不变）
- 两个开关仍默认 `0`，未开启、未部署、未重建生产索引。
