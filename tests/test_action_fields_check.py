"""Copy typed channel test inputs and notes; do not infer expected replies from logs."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_SECRET_KEY'] = 'test-secret'

from app.llm import _explicit_test_action_constraints, _source_projection


def source(channel=7, value='04 A7', note='指示灯闪烁后关闭，发送3次'):
    row = channel + 1
    return {
        'filename': 'ALPHA-2 Communica(HEX)_v2.xlsx',
        'content': (f'工作表《HEX测试》第 {row} 行：B{row}=输出通道号(0-31)：翻转通道{channel}的输出\n'
                    f'（测试：{note}）；F{row}={value}；L{row}=发送指令\n{value}；M{row}=Rcv : 99 88'),
        'table_headers': {'HEX测试': {'columns': {
            'B': '测试项', 'F': 'Input', 'L': '测试步骤', 'M': '实际结果'}}},
    }


def main():
    q = 'ALPHA-2 HEX 测试里，翻转 DO 通道 7 输出的测试动作是什么？'
    row = source()
    assert _source_projection(q, [source(8), row]) == '测试动作：04 A7；指示灯闪烁后关闭；发送3次。[2]'
    assert _source_projection(q.replace('7', '8'), [source(8), row]) == \
        '测试动作：04 A7；指示灯闪烁后关闭；发送3次。[1]'
    for changed in [q.replace('7', '70'), q.replace('DO', 'DI'), q.replace('HEX', 'ASCII'),
                    q.replace('ALPHA-2', 'ALPHA-20'), q.replace('翻转', '打开')]:
        assert not _explicit_test_action_constraints(changed, [row])
    assert not _explicit_test_action_constraints(q, [dict(row, table_headers={})])
    assert not _explicit_test_action_constraints(q, [dict(row, filename='ALPHA-2 HEX.txt')])
    conflicting = dict(row, content=row['content'].replace('L8=发送指令\n04 A7', 'L8=发送指令\n04 A8'))
    assert not _explicit_test_action_constraints(q, [conflicting])
    malformed = dict(row, content=row['content'].replace('F8=', 'F9='))
    assert not _explicit_test_action_constraints(q, [malformed])
    ambiguous = _source_projection(q, [row, source(note='保持打开，发送2次')])
    assert '多条相关资料' in ambiguous and '[1]' in ambiguous and '[2]' in ambiguous
    assert '99 88' not in ambiguous, 'received log is not a prescribed test action'
    changed_value = source(value='02 B1', note='保持关闭，发送4次')
    assert _source_projection(q, [changed_value]) == '测试动作：02 B1；保持关闭；发送4次。[1]'
    print('typed test action and ambiguity boundaries PASS')


if __name__ == '__main__':
    main()
