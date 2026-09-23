"""Deliberate regressions for the isolation checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).
"""

INJECTIONS = [('I1 taskkill 去掉 /T：只杀直接子进程，孙进程变成孤儿',
  'app/parse_runner.py',
  '["taskkill", "/F", "/T", "/PID", str(pid)],',
  '["taskkill", "/F", "/PID", str(pid)],',
  '孙进程已被一并杀掉'),
 ('I2 把崩溃当成超时上报（运维会去调一个没到期的超时值）',
  'app/parse_runner.py',
  'reason="crashed", note=note',
  'reason="timeout", note=note',
  '归为 crashed'),
 ('I3 把解析器报错说成进程崩溃（运维会去找一个不存在的崩溃）',
  'app/parse_runner.py',
  '        code="parse_failed",',
  '        code="parse_crashed",',
  '归为 parse_failed 而不是进程崩溃')]
