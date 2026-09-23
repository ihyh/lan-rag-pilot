"""Deliberate regressions for the latency checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).
"""

INJECTIONS = [('I1 把拒答当成耗时样本（百分位被一堆 0 拉偏）',
  'app/metrics.py',
  '            refusals += 1',
  '            refusals += 1; answered.append(0)',
  '模型耗时样本只含 2 次成功作答'),
 ('I2 失败路径不再记录检索耗时（口径里承诺过）',
  'app/routers/query.py',
  '\n                model=settings.deepseek_model, retrieval_ms=retrieval_ms,',
  '\n                model=settings.deepseek_model,',
  '失败轮次仍记录了检索耗时'),
 ('I3 主路径干脆不测检索耗时',
  'app/routers/query.py',
  '\n    retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)',
  '\n    retrieval_ms = None',
  'retrieval_ms 是非负整数'),
 ('I5 无命中路径不再记录检索耗时（Codex 审核补的那处）',
  'app/routers/query.py',
  '\n        retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)',
  '\n        retrieval_ms = None',
  '无命中仍记录检索耗时'),
 ('I4 百分位不排序（未排序输入算错）',
  'app/metrics.py',
  '    ordered = sorted(values)',
  '    ordered = list(values)',
  '未排序输入也能正确取值')]
