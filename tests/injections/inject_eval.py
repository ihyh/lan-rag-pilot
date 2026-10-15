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
    (
        'I7 不校验查询响应顶层结构',
        'scripts/eval_runner.py',
        '    if not isinstance(response, dict):\n'
        '        raise RuntimeError("查询响应必须是 JSON 对象")',
        '    if False:\n'
        '        raise RuntimeError("查询响应必须是 JSON 对象")',
        '畸形查询响应必须被拒绝: JSON 对象',
    ),
    (
        'I8 不校验查询回答字段类型',
        'scripts/eval_runner.py',
        '    if not isinstance(answer, str):\n'
        '        raise RuntimeError("查询响应 answer 必须是字符串")',
        '    if False:\n'
        '        raise RuntimeError("查询响应 answer 必须是字符串")',
        '畸形查询响应必须被拒绝: answer',
    ),
    (
        'I9 不校验查询来源数组结构',
        'scripts/eval_runner.py',
        '    if not isinstance(actual_sources, list) or not all(isinstance(item, dict) for item in actual_sources):\n'
        '        raise RuntimeError("查询响应 sources 必须是对象数组")',
        '    if False:\n'
        '        raise RuntimeError("查询响应 sources 必须是对象数组")',
        '畸形查询响应必须被拒绝: sources',
    ),
    (
        'I10 让成功响应的无效 JSON 逃逸',
        'scripts/eval_runner.py',
        '                except (json.JSONDecodeError, UnicodeDecodeError) as exc:',
        '                except RuntimeError as exc:',
        '服务返回无效 JSON 时必须转成可记录的查询错误',
    ),
    (
        'I11 接受非对象 JSON 顶层',
        'scripts/eval_runner.py',
        '                if not isinstance(result, dict):\n'
        '                    raise RuntimeError("服务返回的 JSON 必须是对象")',
        '                if False:\n'
        '                    raise RuntimeError("服务返回的 JSON 必须是对象")',
        '服务返回的 JSON 顶层必须是对象',
    ),
    (
        'I12 假定 HTTP 错误体一定是对象',
        'scripts/eval_runner.py',
        '                detail = error_body.get("detail", "") if isinstance(error_body, dict) else ""',
        '                detail = error_body.get("detail", "")',
        '畸形 HTTP 错误体必须保留原始 HTTP 状态',
    ),
]
