# 第三轮：知识库 MCP 小服务 —— 提交说明（待 Codex 复核）

日期：2026-10-20
分支：`feat/manual-document-scope-20261020`（同一分支按轮次独立提交）
本轮基线：上一轮 OCR 提交之后（见本文件所在提交的父提交）
本轮提交：见 `feat(mcp)` 开头的 commit。

## 目标与范围

对应交接文档「第三轮：MCP 小服务（方向已确认）」：把知识库作为 MCP 服务供 DeepSeek 等客户端
查询，**不是**让 RAG 调用外部 MCP 工具。

最小范围已交付：

- `scripts/mcp_server.py`：stdio、只读 MCP 服务（MCP Python SDK v2）。
- `requirements-mcp.txt`：只额外引入 MCP SDK（`mcp>=2.3,<3.0`，v2 接口）。
- `tests/mcp_server_check.py`：用官方 MCP 客户端做协议级验收（未安装 SDK 时 SKIP）。
- `docs/MCP_SERVER.md`：安装、客户端配置、工具契约、数据流向与限制。
- `app/routers/query.py`：把「检索→重排→选段→来源身份」抽成共享函数 `retrieve_sources`，
  网页问答与本服务共用，避免复制索引或另写检索/排序逻辑。

## 工具（全部只读）

| 工具 | 说明 |
| --- | --- |
| `list_documents` | 已就绪文档列表（id/文件名/版本） |
| `search_documents(query, document_ids?, top_k?)` | 带 `document_ids` 的检索；返回文件名/页码/版本/片段内容/排序分数与回显范围 |
| `get_document_text(document_id, page?)` | 按来源身份取原文（按页/段落），标明未经模型生成 |
| `ask_question(question, document_ids?, top_k?)` | 复用网页同一生成路径；`answer` 与 `sources` 分开返回，无证据返回 `no_evidence` |

不包含命令执行、文件删除、重建索引、上传/修改文档等写工具；不写入个人问答历史。

## 复用而非另写一套

- 新增 `retrieve_sources()`：原 `query()` 内联的检索/重排/选段/引用身份逻辑原样搬入，
  网页问答与本服务都调用它；`query()` 只保留 HTTP 语义映射（503/500、拒答文案、响应回显）。
- `ask_question` 直接调用 `app.llm.chat(question, sources, None)`，与网页问答同一生成调用。
- 测试对照：同一问题、同一 `document_ids` 下，网页 `/api/query` 与 MCP `search_documents`
  返回的来源身份集合完全一致（chunk/document/page/paragraph）。
- 不复制索引、不新建向量库、不改变既有实验开关默认值。

## 验证

```powershell
python -m pip install -r requirements-mcp.txt
python tests/mcp_server_check.py
```

测试使用临时 SQLite + mock 嵌入 + 合成文档（无监听端口、无私有文档），通过真实 MCP 客户端
（`ClientSession` + `stdio_client`）完成：initialize、列工具、四个工具调用、未知文档/空查询/
非法 top_k/不存在页码等错误处理、范围过滤、与网页来源一致、问答证据/回答分离、不写历史、
不暴露写工具。

结果：**30/30 通过**。另：全量 `tests/run_checks.py` 已注册本检查（未装 MCP SDK 时 SKIP）。

## 未做 / 未测

- 未部署、未加入任何真实 MCP 客户端配置；未验证 DeepSeek 客户端实际接入体验。
- 未实现 Streamable HTTP / 远程访问 / 鉴权（本机 stdio 最小范围）。
- 未在真实私有文档上验证检索质量；本轮只验证协议、范围与复用一致性。
- MCP 进程与网页服务各自加载嵌入模型/索引；未做共享内存方案（沿用现有进程模型）。
- `ask_question` 会把问题与选中片段发送到配置的模型端点；如配置为外部 API，需用户确认资料流向。

## 已知无关问题（不在本轮范围）

`tests/injections/run_injections.py --group probe` 在本机存在一个与本次改动无关的既有失败
（`I1 恢复 trust_env=True` 注入未变红）；已在基线 `8e05648` 的干净快照上复现，未在本轮修改。
`tests/injections/check_anchors.py` 与 latency 注入组均通过（见全量回归结果）。
