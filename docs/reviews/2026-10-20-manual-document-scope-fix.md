# 第一轮复核修正（R1/R2/R3）提交说明

日期：2026-10-20
分支：`feat/manual-document-scope-20261020`
上一审核基线：`f3b57fe`（Codex 首轮复核的 HEAD）
修复提交：`8e05648`（仅前端修复 + 测试，不含 OCR/MCP）
归档的 Codex 反馈：`docs/reviews/2026-10-20-manual-document-scope-codex-review.md`

上一版提交说明与测试结果保留在 `docs/reviews/2026-10-20-manual-document-scope.md`，未改动；
Codex 的旧失败结果目录 `data/eval/reports/manual-scope-review-20261020/` 也未改动（保留原始观察）。

## 结论

R1/R2/R3 三项 P2 已修复。使用 Codex 自己的复现脚本在**新结果目录**复跑：

- `frontend-results.json`：5/5 用例 `pass: true`（原 3 项失败现已通过）；
- `api-results.json`：15/15 用例通过（后端契约未变，含 101→422、selected+[] 详情、删除后 400）；
- `regression-results.json`：7/7 安全回归命令返回 0。

新结果目录：`data/eval/reports/manual-scope-review-20261020-fix-rerun/`（脚本为 Codex 原脚本副本，
仅把 `run_regressions.py` 里的 Node 路径改为本机路径；旧目录未覆盖）。

## R1 · 首问生成期间仍能改动文档

**处理**：生成中所有会改变范围的入口都被禁用并加程序级防护（旧节点事件/程序调用也拦得住）：

- `toggleDocument` 在 `S.busy` 或会话已固定时直接拒绝，并返回是否生效；
- 复选框、每行「仅选」、已选 chip 的删除按钮在生成中 `disabled`；勾选被拒时把 DOM 勾选状态回退到模型状态；
- 全选、清空、刷新在生成中禁用（此前只有单选模式与部分按钮禁用）；
- 范围提示在生成期间显示「正在生成回答，本次检索范围已固定、暂时不能修改」；
- 失败/停止后 `S.busy` 复位，草稿范围仍可继续编辑；成功后沿用服务器返回的真实范围。

**复测**：
`tests/scope_selection_check.js` 新增 §18：用可控 gate 挂起首问请求，断言复选框/「仅选」禁用、
chip 删除、全选、清空、程序级勾选均不改变草稿，在途请求 `document_ids` 保持发出时的选择；
请求完成后 `currentScopeMode=selected`、`currentDocumentIds` 与请求一致。
真实浏览器新增 §8c：用 `[[mock:sleep:3]]` 让生成停留 3 秒，断言生成中复选框/「仅选」禁用、
勾选别的文档被回退、提示「正在生成」、在途载荷只有发出时选择的文档、完成后显示服务器范围。

## R2 · 历史恢复忽略服务端 `scope_mode`

**处理**：

- `renderConversation` 把 `conversation.scope_mode` 传给 `setDocumentScope(ids, locked, mode)`；
- 已固定会话**优先使用并校验**服务端模式（只接受 `all`/`selected`），仅在旧响应没有该字段时按 ID 推断；
- `selected + 空 document_ids`（原选定文档已删除或不再可见）显示为
  「仅选中文档 · 原文档不可用（已固定）」，锁定行说明「首问选定的文档已删除或不再可见，无法显示文件名」
  并引导新建对话；不伪装为全库，也不暴露被过滤的 ID；
- `all + []` 仍显示全库；`selected + 非空 ID` 正常显示文件名。

**复测**：
`tests/scope_selection_check.js` 新增 §19，覆盖 `selected+非空`、`selected+[]`、`all+[]`、
无 `scope_mode` 的旧响应（空/非空 ID 两种推断）；首问结束后的详情恢复走同一函数。
Codex 复现脚本第 2 项现为 `actual: selected`，徽标为 `仅选中文档 · 原文档不可用（已固定）`。

## R3 · 全选不遵守 100 文档上限

**处理**：后端 `max_length=100` 保持不动，前端新增 `MAX_SCOPE_DOCUMENTS = 100` 并在所有入口校验：

- 「全选筛选结果」加入后超过 100 时**整批拒绝**，不加入任何文档（不做静默截取），提示当前数量与上限；
- 逐一勾选第 101 个被拒绝并回退勾选显示；
- 设备快捷按钮匹配超过 100 时拒绝替换并说明；
- 发送前 `scopeBlockReason()` 对 `>100` 返回明确提示（即使通过程序注入状态也拦得住）；
- 卡片说明文案补充「最多可选 100 个文档」。

**复测**：
`tests/scope_selection_check.js` 新增 §20：101 份文档点全选 → 0 个加入且发送前校验非空；
程序注入 101 个 → 不发出请求且保留输入；逐一勾选 100 个成功、第 101 个被拒并回退；
100 个文档可以发送（载荷 `document_ids` 长度 100）。
`tests/api_smoke.py` 新增边界：101 个 → 422；恰好 100 个通过长度校验（随后按文档有效性 400/404）。
Codex 复现第 5 项现为 `chosen: 0`、`blockReason` 非空。

## 本轮新增/更新的回归命令

```powershell
node tests/scope_selection_check.js
node tests/device_scope_check.js
node tests/citations_check.js
node --check app/static/js/app.js
python -X utf8 tests/conversation_scope_check.py
python -X utf8 tests/document_acl_check.py
python -X utf8 tests/windows_release_check.py
python tests/run_checks.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/smoke_runner.ps1 -Python <venv python>
```

## 复跑结果（均为修复提交 `8e05648` 之后的代码）

| 验证 | 结果 |
| --- | --- |
| Codex 前端复现（新目录） | 5/5 用例通过（原 3 项失败） |
| Codex API 复现（新目录） | 15/15 用例通过 |
| Codex 7 条安全回归命令（新目录） | 7/7 返回 0 |
| `tests/run_checks.py` 全量离线 | 35/35 通过 |
| `tests/smoke_runner.ps1` 端到端 | 218 项 API 断言 + 重启后 22 项，全部通过 |
| 真实浏览器（headless Edge 155，桌面 + 390×844 移动视口） | 47/47 通过（新增 R1 生成期间锁定 5 项） |

浏览器原始结果汇总：`.verify/artifacts/browser-results.log`（仓库上级，未提交）；
脚本与运行入口：`.verify/scope_browser_check.cjs`、`.verify/run_browser_check.ps1`；
截图：`.verify/artifacts/desktop-selected-scope.png`、`.verify/artifacts/mobile-selected-scope.png`。

## 未改 / 仍未验证

- 后端 100 上限未改：保留原 Pydantic `max_length=100` 的设计；若需提高上限应另立任务说明依据与代价，而不是只删校验。
- 实体手机、Tailscale HTTPS、Safari 与软键盘仍未验证（与上一轮相同，未为此引入新框架）。
- R2 的“文档被撤权”真实浏览器场景未复现：管理员账号不受可见性过滤影响；该组合由 Codex `api_reproduce.py`
  （独立构造 selected+[] 详情）与前端沙箱用例覆盖。
- 未提交、未推送、未合并、未部署；本说明不构成验收通过。

## 逐项对照 Codex 复测要求

| Codex 要求 | 覆盖位置 |
| --- | --- |
| R1：首问生成中 checkbox、仅选、chip 删除、设备/全选/清空均不能改范围；失败/停止后可编辑；成功后用服务器范围 | `scope_selection_check.js` §18、浏览器 §8c；设备/全选/清空在 `updateScopeActionLabels` + 事件处理器 busy 防护 |
| R2：selected+非空、all+[]、selected+[]、旧响应；刷新/历史/首问结束一致 | `scope_selection_check.js` §19；浏览器刷新与历史恢复断言；`renderConversation` 三个调用点统一走 `setDocumentScope` |
| R3：100 可发送、101 不能发请求且保留输入/选择；全选、筛选累加、逐一勾选、设备入口 | `scope_selection_check.js` §20；`api_smoke.py` 100/101 边界；设备入口长度校验 |
