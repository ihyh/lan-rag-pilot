# 文档来源资格检查（隔离开发，尚未接入查询接口）

`app/evidence_eligibility.py`将服务端取得的文档切片绑定为只能提供原文的来源。它不判断自然语言问题的意图，不自动给文本标注“定义/测试/维修”，也不校验原文业务含义。

```python
from app.evidence_eligibility import bind_documents, packet_digest, render_document_selection

# rows必须由服务端按当前用户权限检索取得，不能采用模型返回的来源内容。
packet = bind_documents(rows)
pinned_digest = packet_digest(packet)  # 由调用方保留，不能让模型提交或替换
result = render_document_selection(
    packet, pinned_digest,
    {"chunk_ids": [rows[0]["chunk_id"]]},  # 模型仅可提议现有切片ID
    purpose="source_excerpt",  # 调用方明确给定，不读取模型的purpose标签
)
```

来源必须包含`chunk_id/document_id/filename/document_sha256/document_version/content`，可含`page/paragraph`。文档版本仅表示文档身份，不表示设备固件版本。额外的`role/device_id/observed_at/origin/capabilities`字段不会赋予权限；来源正文与身份绑定在调用方保留的摘要中。

| 调用方指定用途 | 行为 |
|---|---|
| `source_excerpt` | 原样显示所选文档文字及原始来源编号，明确这是资料记录 |
| `current_value` | 返回无法确认现场状态的缺口提示，不将文档文字作为当前状态答案 |
| `onsite_remedy` | 返回缺少当前设备/故障适用性核验的提示，不将文档文字作为确定维修方法 |

没有真实观测采集或维修适用性核验通道时，上传文档不具备这两类现场凭据。维修手册、历史日志仍可按资料原文查询。这里没有创建遥测通道，也没有把所有“如何操作”问题自动判为现场维修。

模型提议只能含`chunk_ids`整数列表；额外答案、用途、标签、未知/重复编号均拒绝。返回的`blocks.content`和`text`是纯文本，接入前端时须使用现有安全文本渲染方式，不应作为HTML执行。输出始终保留`semantic_verified=false`、`onsite_verified=false`。

当前查询来源尚未附上文件SHA及版本；正式接入前需要从服务端JOIN取得这些字段，绑定ACL范围，并确定可信的请求用途入口。不能用未经验证的模型意图分类绕过这个边界。当前模块不在普通问答、流式输出或生产服务中生效。

运行独立检查：

```powershell
python tests/evidence_eligibility_check.py
```

检查覆盖可查文档、现场用途拒绝、来源绑定、元数据注入、模型附加文字、JSON往返和引用编号。这些是程序边界检查，不是问答正确率测评。
