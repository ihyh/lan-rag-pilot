# 故障排查

[返回首页](../README.md)

先记录时间、操作、错误码和部署版本。不要把 .env、Cookie、密码、完整内部文档或未脱敏日志发到公开渠道。运行机排错仍必须离线，不能靠打开互联网掩盖缺包/配置错误。

## 从哪一层查

1. 页面完全打不开：查 RAG/代理进程、监听、DNS、证书及允许网段。
2. 页面能开但 ready 失败：查本地嵌入目录、依赖、内存。
3. ready 正常但提问失败：查 Ollama、模型标签、兼容接口和生成资源。
4. 能回答但不准确/没来源：查解析、文档状态、范围、检索与历史引用。

Ubuntu 在部署目录用 `docker compose ps`、`docker compose logs --tail 100 rag`、`docker compose logs --tail 100 ollama`。Windows 看启动窗口及 Ollama 本地日志。先使用只读检查，别删除数据重装。

## 修改密码、重置密码、设置角色无反应

这些操作共享前端表单逻辑，“点击没反应”并不必然是后端故障。

- 强制刷新页面后重试，确认应用模板与静态文件来自同一发布包；代理不能混用旧缓存。
- 开发者工具 Network 中点击确认：没有请求则看 Console 的脚本错误和表单校验；请求已发出则看实际状态码。
- 401：会话失效，重新登录；403：角色不足或来源校验失败，检查 root 权限及代理 Host/协议；400/422：按返回提示检查字段；5xx：按请求时间核对服务日志。
- HTTPS 页面应使用 Secure Cookie；在 HTTP 地址测试 Secure=true 会导致登录状态不持久。修正入口，不要在 LAN 长期关闭 Secure。
- 管理员重置已有用户应使用管理界面；修改 RAG_ROOT_PASSWORD 不会重置现有库账号。

维护者在独立开发副本运行 `node tests/form_modal_check.js`。该回归检查不是生产用户密码修改操作；不要直接编辑密码哈希或删库来排错。

## 模型连接错误、404 或超时

| 表现 | 核对 |
|---|---|
| llm_network / ConnectError | Ollama 是否运行，RAG 所处环境能否到正确地址 |
| HTTP 404 | BASE_URL 是否指向兼容接口 /v1，模型标签是否真的存在 |
| 身份/API Key 配置错误 | 本地 Ollama 示例用 DEEPSEEK_API_KEY=ollama，不是公网凭据 |
| 超时 | 模型首次加载、CPU/GPU 分配、上下文与并发、RAG/代理超时 |
| HTTP 502 / "网关错误" | 请求被系统或环境代理接管。模型服务是本机/内网依赖，不该走代理；本产品默认已不使用代理，见下方“经代理访问模型服务” |
| 健康正常仍生成失败 | `/api/ready` 现在会校验生成模型，但仍不等于“能答对”；检索质量、拒答阈值与语料覆盖要另行用真实提问核对 |

Windows 查 `ollama list` 和 `Invoke-RestMethod http://127.0.0.1:11434/api/tags`。
Ubuntu 查 `docker compose exec ollama ollama list`；RAG 容器中的 127.0.0.1 是它自己，不是宿主机，也不是 Ollama 容器。本文 Ubuntu 配置使用 `http://ollama:11434/v1`。

配置改完须重启 Windows 应用；Compose 用 `docker compose up -d --pull never --no-build`，不是仅 restart。模型缺失应导入新离线包，不在线 pull。

### 经代理访问模型服务（502 / 网关错误）

症状：提问全部失败，错误为 `llm_upstream` 或信息里出现 502；而 Ollama 进程正常、`ollama list` 也能列出模型。成因是请求没有发到模型服务，而是被送进了系统或环境里的 HTTP 代理，代理无法转发本机/内网地址就回 502。

要点：

- 本产品**默认不使用代理**（`RAG_LLM_TRUST_ENV_PROXY=0`）。Windows 上 httpx 会经 urllib 读取注册表里的 WinINET 系统代理，且**不读 `ProxyOverride` 绕过列表**——系统里写着 `127.*` 不走代理也不生效，所以必须由程序侧关掉。
- 部署机上开着 Clash 一类代理客户端时无需为其添加绕过规则；这是刻意设计。
- 如果这个部署确实要经代理访问公网 API，才设 `RAG_LLM_TRUST_ENV_PROXY=1`，并自行确认代理能转发到模型地址。
- 排查手法：用 `curl`（会走系统代理）与不带代理的直连各请求一次 `/v1/models`，两者结果不一致即可确认。`/api/ready` 的 `checks.llm.message` 会直接指出这一原因，不必自行猜测。

## ready 一直 503 / 模型缓存缺失

先看响应体的 `reason`，两条链路要分开排查：

- `reason=embed_not_ready`：嵌入模型这条链路。检查 RAG_EMBED_BACKEND=st、本地 RAG_EMBED_MODEL 目录及权限。完整目录需含 tokenizer、配置和权重，不能只放一个模型文件。HF 离线变量必须保留；修复方式是补齐管理员包而非改成联网。如果后端为 mock，ready 即使成功也不代表真实检索可用，mock 只用于隔离测试。真实嵌入在 CPU 运行，加载期间可能较慢；查看日志判断加载进度或内存不足。
- `reason=llm_not_ready`：生成模型这条链路。响应体 `checks.llm.message` 会说明具体原因，常见情况包括：`DEEPSEEK_API_KEY` 缺失或含控制字符；Ollama 没运行、地址写错、发生重定向或被防火墙拦截；配置的模型标签在清单中不存在；401/403 鉴权失败；402 额度不足；429 限流；以及被代理接管产生的网关错误（见上一节）。公开诊断不会回显密钥、模型地址或服务上的模型清单。注意嵌入未就绪时系统不会去探测模型服务，所以这一步不会掩盖嵌入问题。

探测结果默认缓存 30 秒（`RAG_READY_PROBE_TTL_S`），因此刚修好 Ollama 后 `/api/ready` 最多要等这么久才转绿；需要立刻确认真实状态就用 `RAG_READY_PROBE_TTL_S=0` 或重启服务。

## 没有引用来源

- 等待本轮流式完成，再展开“查看引用来源”。
- 确认回答不是网络失败、取消或资料不足的拒答；这些不一定有来源。
- 在管理页检查文档 ready、解析文字与所选设备/文档范围。扫描 PDF/表格图片没有内置 OCR。
- 在浏览器响应/流结束数据和服务器记录中核对是否确实返回 citations；有数据但没展示查前端，无数据查检索链路。诊断材料需留在受控环境。
- 删除/重处理文档会替换片段或移除原文，旧回答的来源可能不再可访问；从对应备份核查，不伪造一个来源补上。

有来源也可能答非所问。按 [业务评测](../eval/README.md)检查人工标准答案，不能把相似度当答案置信度。

## 回答一次性出现而非逐字显示

当前前端使用流式生成。检查浏览器请求是否为流式路径、代理是否缓冲、proxy_read_timeout 是否足够，并确认前后端版本一致。无检索结果/错误路径不一定产生 token 流。引用一般在完成时展示，不需要为此伪造中间引用。

## 上传失败或检索不到

- 413：核对应用默认 25 MB 与 Nginx 限制；不要只提高代理限制。
- 文件类型错误：真实类型与扩展名必须一致；旧 DOC 需 antiword，XLS 不等于 XLSX。
- 扫描 PDF 或嵌入图片：先使用获准的离线 OCR/转换并人工核对。
- 嵌入维度不一致：停止混入，按 [模型重建](MODELS.md)在独立实例处理；同维度换模型也要重建。
- 资料有答案但拒答：检查解析、切片、范围和检索命中，再评估阈值。不要直接把阈值降到零或换大生成模型。
- 参数改了没生效：Top-K、限流、并发可能已保存在数据库运行设置中，环境只提供初始默认值。

## 运行慢、显存不足、磁盘满

**先分清是"读提示词"慢还是"写答案"慢**，两者的解法完全不同。Ollama 原生接口会返回精确耗时：

```bash
curl -s http://127.0.0.1:11434/api/generate -d '{
  "model":"qwen3:1.7b","prompt":"测试","stream":false,"options":{"num_predict":64}
}' | python3 -c "import json,sys; d=json.load(sys.stdin); \
print('读提示词 %.1f tok/s' % (d['prompt_eval_count']/(d['prompt_eval_duration']/1e9))); \
print('生成答案 %.1f tok/s' % (d['eval_count']/(d['eval_duration']/1e9)))"
```

纯 CPU 笔记本（i5-13420H，无独显）上的实测参考值：

| 环节 | 实测 | 说明 |
|---|---|---|
| 读提示词 | ~49–61 token/s | **主要瓶颈**：RAG 每次要送入 800–1800 token |
| 生成答案 | ~12–16 token/s | 1.7B 模型的正常水平，不是故障 |
| 模型冷加载 | ~25 秒 | 闲置 5 分钟后重新提问才付这个代价 |
| BGE 检索 | 60–530 ms | 基本可忽略 |

按性价比排序的提速手段：

1. **提高模型驻留时长**（上表 3）：给 Ollama 服务设 `OLLAMA_KEEP_ALIVE=30m`（或 `-1` 永不卸载）。
   注意必须设在 **Ollama 服务端**——实测在请求体里传 `keep_alive` 对 OpenAI 兼容端点
   `/v1/chat/completions` **无效**。
2. **降低 `RAG_TOP_K`**（上表 1）：5 → 3 可让单次问答从 32~43 秒降到 18~22 秒。可在管理页
   “运行参数”在线调整，无需重启。改动前请用真实评测集确认召回率没有下降。
3. **保持流式输出**：首字 2~3 秒出现，避免"转圈 40 秒"的体感。
4. 调小切片长度需要重新索引全部文档，代价大，只在确有必要时做。

### 突然变得极慢、甚至不出回答：先查内存和残留进程

Windows 上 `ollama serve` 的模型进程是独立的 `llama-server.exe`，**结束 `ollama serve` 不会
连带结束它**。重启过 Ollama、或同时存在多个模型实例时，可能留下孤儿进程长期占用内存：

```powershell
Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
  ForEach-Object { "PID $($_.ProcessId)  $([int]((Get-Process -Id $_.ProcessId).WorkingSet64/1MB))MB  父=$($_.ParentProcessId)" }
Get-CimInstance Win32_OperatingSystem |
  ForEach-Object { "可用 $([int]($_.FreePhysicalMemory/1KB)) MB" }
```

正常应只有 **1 个** `llama-server`。多于 1 个说明有孤儿，结束它们即可。
内存不足时 Windows 会把模型和应用一起换页到磁盘，表现为所有请求都变慢、流式回答长时间没有输出。

另外注意 `ollama ps` / `ollama list` 受 `OLLAMA_HOST` 影响：如果该变量指向另一台机器或另一个
实例，你会看到错误的状态（例如明明在跑却显示为空）。要查本机实例，直接用
`http://127.0.0.1:11434/api/ps`。

磁盘满先暂停上传，检查日志、备份和模型占用。按保留策略清理已确认可回收的副本；禁止直接删除 data、业务卷或不明数据库 WAL。保留至少一个已恢复验证的备份。

## 备份校验失败

校验 hash、路径、可用空间和权限。restore_check 成功仅是结构检查，还要在新实例验证登录、原文、历史引用和新问答。失败先保留原库/归档，不能覆盖生产“试试看”。
