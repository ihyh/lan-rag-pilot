# 知识库 MCP 小服务（第三轮）

状态：**已实现并协议级验证；未部署、未开放远程传输、未合并。**

对应交接文档「第三轮：MCP 小服务（方向已确认）」：把知识库作为 MCP 服务，供 DeepSeek 等
客户端查询。本轮**不是**让 RAG 调用外部 MCP 工具。

## 传输与 SDK

- 只提供 **stdio** 传输：本机客户端拉起进程通信，不新开监听端口、不暴露公网/VPN。
- MCP Python SDK **v2**（`mcp>=2.3,<3.0`）。v2 把 v1 的 `FastMCP` 改名为 `MCPServer`，
  本服务按 v2 接口编写（`from mcp.server.mcpserver import MCPServer`）。
- 远程/Streamable HTTP 未实现；确有远程客户端需求时再单独评估（需鉴权与传输安全设计）。

## 安装与运行

服务复用项目既有依赖（FastAPI/Pydantic/numpy 等是项目本身的要求），只需额外安装 MCP SDK：

```powershell
# 项目默认依赖已安装后
python -m pip install -r requirements-mcp.txt
# 从项目目录以 stdio 启动（由 MCP 客户端拉起，不需要手动常驻）
python scripts\mcp_server.py
```

客户端配置示例（stdio JSON，路径替换为本机项目目录）：

```json
{
  "mcpServers": {
    "personal-kb": {
      "command": "C:\\rag\\.venv\\Scripts\\python.exe",
      "args": ["C:\\rag\\scripts\\mcp_server.py"],
      "env": { "RAG_DATA_DIR": "C:\\rag\\data" }
    }
  }
}
```

环境变量与网页服务相同（`RAG_DATA_DIR`、`RAG_DB_PATH`、`RAG_EMBED_BACKEND`、
`RAG_MODELS_DIR`、`DEEPSEEK_BASE_URL` 等）。MCP 进程首次工具调用时才加载数据库/索引/嵌入模型；
真实 BGE 模型加载需要时间和内存，和网页服务同时运行会有两份模型内存占用，属已知代价。

## 工具（最小范围，全部只读）

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `list_documents` | 无 | 已就绪文档：`document_id`、`filename`、`version`、`total` |
| `search_documents` | `query`、`document_ids?`、`top_k?`（1–20，默认 5） | 带来源身份的原始片段：`chunk_id/document_id/filename/version/page/paragraph/score/excerpt/content`，以及 `scope_mode`、`document_ids`、`retrieval_ms` |
| `get_document_text` | `document_id`、`page?` | 原始文字按页/段落返回：`filename/version/pages[].paragraphs[].content`；`note` 标明未经模型生成 |
| `ask_question` | `question`、`document_ids?`、`top_k?` | `answer`（生成回答）+ `sources`（原始证据身份/摘录）+ `notice` + `model/latency_ms`；无证据时 `status=no_evidence`、`answer=null` |

范围语义与网页问答一致：`document_ids` 省略或空 = 全库；显式列表必须是已就绪文档的子集，
越界/不存在会返回可读错误；限定后检索与取原文都不越界。

**不提供**：命令执行、文件删除、重建索引、上传/修改文档、问答历史写入等写操作；
工具返回值里不出现任意命令或文件系统路径。

## 复用同一份检索与问答路径（不复制索引）

`app/routers/query.py` 中原有的「构造检索问题 → 嵌入 → 向量/词法检索 → 重排 → 选段 →
引用身份」逻辑抽成共享函数 `retrieve_sources(db, question, document_ids, top_k, history)`，
网页问答与 MCP 的 `search_documents`/`ask_question` 都调用它：

- 同一份文档范围约束（`document_ids` 校验、空集合语义）；
- 同一套检索/排序/选段与 `score` 语义；
- 同一份来源身份（chunk/document/文件名/页码/段落），不复制索引、不另写答案逻辑。

`ask_question` 直接复用 `app.llm.chat`（网页问答使用的同一生成调用与提示路径），
不额外实现第二套提示或拼接逻辑。

## 验证

```powershell
# 需要 requirements-mcp.txt；未安装时测试打印 SKIP 并退出 0
python tests/mcp_server_check.py
```

测试使用临时 SQLite + mock 嵌入 + 合成文档（不启动监听端口、不使用私有文档），
通过**真实 MCP 客户端**（官方 SDK `ClientSession` + `stdio_client`）完成：

- initialize（服务名/协议版本）、列工具；
- `list_documents`、`search_documents`（全库与限定范围）、`get_document_text`（整篇/指定页）；
- 错误处理：未知 document_ids、未知文档、空查询、非法 top_k、不存在页码均返回可读错误；
- 全库检索能召回两份相似文档，限定范围后结果不越界；
- **与网页问答一致**：同一问题、同一 `document_ids` 下，网页 `/api/query` 返回的来源身份集合与
  MCP `search_documents` 完全一致（复用同一函数）；
- `ask_question` 区分生成回答与原始证据；工具调用**不写入** conversations/chats 历史；
- 工具列表不含删除/重建索引/命令执行类工具。

结果：**30/30 通过**（详见 `docs/reviews/2026-10-20-mcp-server.md`）。

## 数据流向与安全边界

- stdio 本机进程：数据不经过网络端口；MCP 服务与本机数据库同权限，属于本机单用户只读服务。
- `ask_question` 会把问题与选出的**原始片段**发送到当前配置的生成模型端点（默认本机 Ollama；
  若配置成外部 DeepSeek API 则资料会离开本机）。接入外部模型客户端前，需由用户确认资料流向；
  MCP 本身不改变这一数据路径。
- MCP 提供调用入口，**不直接提升答案准确率**；检索分数不是正确率，`answer` 是生成内容。
- 本地单用户场景不引入账号/ACL 过滤；管理员/普通用户差异仍只在网页服务内生效。

## 未做 / 边界

- 未部署、未加入开机自启、未配置到任何真实客户端（只做协议级验证）。
- 未实现 Streamable HTTP、鉴权、远程访问。
- 未把 MCP 查询写入个人问答历史（有意只读；如需要另议）。
- MCP 进程与网页服务各自加载嵌入模型/索引（沿用现有进程模型，未做共享内存方案）。
