# CEID条目入库与冲突问答

CEID定义表中的每个编号条目独立入库。同ID多行、跨场景和不同文档均保留，
`ceid_events`不对CEID做唯一约束。条目跨段落或页时不按普通token窗口切开；
普通文字仍使用原切块规则。PDF重复页眉从目录元数据中屏蔽，原chunk文字保留。
支持`CEID Collection Event ID`/`CEID description`表内的`数字 = 文字`（含逐词换行），
显式`CEID=数字: 文字`行，以及带CEID/名称/场景/描述列头的DOCX/XLSX表。
只有数字但无CEID表头时不推断其角色，避免把RCMD/SVID识别成事件。

结构化目录字段：`ceid`（十进制字符串）、`event_name`、`scenario`、
`full_definition`（提取到的整个条目）、`description_status`、
`document_id`、`chunk_id`和结束页`page_end`。来源文件/起始页通过chunks/documents关联：

```sql
SELECT e.ceid AS CEID, e.event_name AS 事件名称, e.scenario AS 业务场景,
       e.full_definition AS 条目原文, e.description_status AS 描述状态,
       d.filename AS 文档来源, c.page AS 起始页, e.page_end AS 结束页
FROM ceid_events e
JOIN documents d ON d.id=e.document_id
JOIN chunks c ON c.id=e.chunk_id
ORDER BY d.id, c.seq, e.id;
```

`label_only`表示只有标签/未识别到独立描述字段，绝不靠模型补全文义。
`provided`表示存在明确Description/Definition或描述列，不代表程序验证其业务完整性。
PDF删除线等视觉修订状态不由普通文本提取推断；保留多版本并交人工核对。

检索保留现有关键词+向量两路；数字ID另走CEID精确目录及显式数字引用，排除11/111串号。
名称查到的ID会再次查找其在当前可见文档范围内的全部定义，绕过向量阈值和Top-K截断。
例如`load cycle的事件ID`依本流程查开始事件，完成/预加载/卸载事件不混入开始清单；
同号的其他场景仍列出。需要完成事件时应明确问`load cycle completed的事件ID`。
引用编号根据本次来源位置生成，不固定使用历史截图中的[3]。

最终回答由目录原文投影，普通问答继续走原生成流程；系统提示也要求CEID全部列出、
逐场景引用、不仲裁和缺失说明。相关资料超过单次上下文预算时明确拒绝截取子集，
返回`ceid_context_limit`，需缩小文档范围。权限限制仍然生效，不因同号扩展泄露隐藏文档。

已有文档不会在应用启动时自动重建。旧索引可兼容识别已有CEID表，但恢复以前被切断
的条目需要从原文件重新入库。发布时应先备份、部署代码并初始化新表，再按管理员选择
重建涉及的SECS文档；此次开发没有修改运行中的个人服务器或生产索引。

公开验证：`python tests/ceid_check.py` 和 Windows 隔离 smoke，使用合成来源和 mock 嵌入，
主要证明精确 ID、冲突扩展及回答链路，不作为语义召回率或全库正确率成绩。
真实原文的本机隔离测试证据单独保留，不随源码分发，也不是公开测试的运行依赖。
