"""Deliberate regressions for the real-question evaluator checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).
"""

INJECTIONS = [
    (
        'I1 接受没有标准来源的非拒答样本',
        'scripts/eval_runner.py',
        '        if not should_refuse and not sources:\n'
        '            raise ValueError(f"第 {line_no} 行非拒答样本必须包含 expected_sources")',
        '        if False:\n'
        '            raise ValueError(f"第 {line_no} 行非拒答样本必须包含 expected_sources")',
        '非拒答样本必须包含 expected_sources',
    ),
    (
        'I2 把人工复核答案重新计入自动通过',
        'scripts/eval_runner.py',
        '        and citation_location_hit and answer_keywords_hit,',
        '        and citation_location_hit and answer_check != "fail",',
        '需要人工复核的答案不得计入 auto_pass',
    ),
    (
        'I3 取消连续评测请求节流',
        'scripts/eval_runner.py',
        '            if remaining > 0:\n'
        '                time.sleep(remaining)',
        '            if False:\n'
        '                time.sleep(remaining)',
        '连续评测请求应按最小间隔节流',
    ),
    (
        'I4 把未知 Top-K 的指标重新命名为 Top-5',
        'scripts/eval_runner.py',
        '            "document_hit_rate": round(',
        '            "top5_hit_rate": round(',
        '摘要必须使用不假定 Top-K 的 document_hit_rate',
    ),
    (
        'I5 把查询失败的拒答题混入可回答题分母',
        'scripts/eval_runner.py',
        '                "expected_refusal": bool(case["should_refuse"]),',
        '                "expected_refusal": False,',
        '拒答题即使查询失败也不得混入可回答题分母',
    ),
    (
        'I6 接受会关闭节流或使 sleep 崩溃的非有限间隔',
        'scripts/eval_runner.py',
        '    if not math.isfinite(min_interval) or min_interval < 0:',
        '    if min_interval < 0:',
        '非法 min_interval 必须在创建客户端前被拒绝',
    ),
]
