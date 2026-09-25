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
    (
        'I5 恢复多文件共享单一型号词的关键词提升',
        'app/index.py',
        '                if live_terms and not broad_filename_term:',
        '                if live_terms:',
        '多文件共享的单一型号词不得压过语义结果',
    ),
    (
        'I6 去掉融合前的关键词候选范围过滤',
        'app/index.py',
        '        keyword_ids = [cid for cid in keyword_ids if cid in positions]',
        '        keyword_ids = list(keyword_ids)',
        '范围外或陈旧关键词候选不得导致检索崩溃',
    ),
    (
        'I7 非目标 KeyError 不得冒充范围失败',
        'app/index.py',
        '        keyword_set = set(keyword_ids)',
        '        if query_text.startswith("PLUS-500 的 SECS 测试"):\n'
        '            positions.pop(2, None)\n'
        '        keyword_set = set(keyword_ids)',
        '非目标 KeyError 不得被归类为不可见文档候选',
    ),
]
