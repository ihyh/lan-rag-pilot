"""Deliberate regressions for the hybrid retrieval checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).
"""

INJECTIONS = [
    (
        'I1 恢复"所有术语全要命中"的 AND',
        'app/index.py',
        '                live_terms = [term for term in terms if self._keyword_exists(term, document_ids)]',
        '                live_terms = list(terms)',
        '含不存在术语时精确匹配仍须生效',
    ),
    (
        'I2 只在全库判断术语是否存在，忽略当前文档范围',
        'app/index.py',
        '                live_terms = [term for term in terms if self._keyword_exists(term, document_ids)]',
        '                live_terms = [term for term in terms if self._keyword_exists(term, None)]',
        '范围外术语不得清空精确匹配',
    ),
    (
        'I3 关闭文件名约束回退',
        'app/index.py',
        '                if (not keyword_ids and len(live_terms) >= 2) or title_only:',
        '                if False:',
        '文件名限定文档后应召回 AUTO 指令',
    ),
    (
        'I4 忽略标题独有术语',
        'app/index.py',
        '                if (not keyword_ids and len(live_terms) >= 2) or title_only:',
        '                if (not keyword_ids and len(live_terms) >= 2) or False:',
        '标题独有型号应限定正文术语检索的文档',
    ),
]
