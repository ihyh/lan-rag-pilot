"""Deliberate regressions for the hybrid retrieval checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).
"""

INJECTIONS = [('I1 恢复"所有术语全要命中"的 AND（一个零倒排项的术语就让精确匹配静默失效）',
  'app/index.py',
  '                live_terms = [term for term in terms if self._keyword_exists(term)]',
  '                live_terms = list(terms)',
  '含不存在术语时精确匹配仍须生效')]
