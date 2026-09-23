"""Deliberate regressions for the chunker structure checks.

Each tuple is (label, repository path, original, broken, expected failed assertion).

两条注入分别覆盖两条切分路径：offsets 路径（真实 tokenizer）与字符近似路径（mock 后端）。
期望断言里带路径标签，用来确认是**对应那条路径**变红，而不是另一条顺带报错。
"""

INJECTIONS = [
    (
        'I1 去掉 offsets 路径的最小跨度约束（断点可紧贴起点）',
        'app/chunking.py',
        '            elif snap is not None and (snap + 1 - start) >= need:',
        '            elif snap is not None:',
        'offsets/字符级',
    ),
    (
        'I2 去掉字符近似路径的最小跨度约束',
        'app/chunking.py',
        '            j = boundary if (boundary is not None and boundary - i >= need) else j',
        '            j = boundary if boundary is not None else j',
        '字符近似路径',
    ),
]
