# 来源检查的隔离调用路径

`app/evidence_request.py`负责把现有权限、来源绑定与输出检查连接起来。它尚未挂到普通问答路由；HTTP适配器仅存在于`tests/evidence_request_check.py`，在进程内使用，不开启端口。

调用顺序：

1. 从已认证会话取得用户ID，执行原有ACL限定的候选检索。
2. 调用`prepare_evidence_request(db, user_id, candidate_chunk_ids, document_ids=..., purposes=...)`；再次读取当前用户与权限，取得数据库中的文档SHA、版本和正文，生成仅由服务端持有的不可变receipt。
3. 模型只提出`{"chunk_ids": [...]}`。缺少明确用途时可跳过模型，进入用途确认提示。
4. 调用`finish_evidence_request(db, user_id, receipt, proposal)`；重新读取用户权限和来源，发现撤权、账号停用、文档不可用或正文/版本变化时拒绝输出。
5. 普通响应使用检查后的reply；流式响应使用`evidence_events(reply)`的NDJSON帧，不转发原始模型delta。

用途由用户明确选择或可信动作入口提供，在模型选择之前绑定。支持`source_excerpt/current_value/onsite_remedy`；可明确请求多项。混合请求分别保留资料原文与现场缺口，不将整段资料查询一并拒绝。缺失用途返回`needs_clarification`，未知/空/重复用途拒绝。这里没有自然语言意图分类器。

权限复用`acl.effective_scope`和真实用户/组授权；None与空set含义保持区分。显式文档范围、候选来源必须全部存在、ready且可见；否则统一`source_unavailable`，不泄露未授权文件信息。管理员权限通过现有`is_kb_admin`逻辑映射。receipt不能跨用户使用，也不能让客户端或模型提交/修改。

检查发生在准备输出的读取阶段。应使用项目的每请求独立自动提交连接；本模块不提交调用方事务。它不承诺传输过程中即时撤回已经发出的内容，也没有提供数据库跨语句并发撤权的原子性保证。

引用用显式`source_id`匹配，不依赖返回数组下标。例如只选择第二个来源时，引用仍是`[2]`且sources的source_id为2。前端接入必须按此映射；当前旧前端尚未修改。NDJSON为sources、一个经过检查的文本delta、done三帧；这是缓冲后输出，不是实时逐token生成。

运行：

```powershell
python tests/evidence_request_check.py
```

检查在内存SQLite中使用现有SCHEMA、真实会话Cookie依赖和ACL函数，经进程内ASGI验证JSON/NDJSON。它覆盖权限、撤权、来源变化、混合/缺失用途及流式校验，不代表检索质量、自然语言意图、完整生成答案、浏览器渲染、CSRF或限流验收。没有模型调用、生产写入或新依赖；正常生产路由不受此候选影响。
