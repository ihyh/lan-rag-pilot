"""Synthetic row-address, scope and test-input regressions; no production writes."""
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RAG_EMBED_BACKEND'] = 'mock'
os.environ['RAG_SECRET_KEY'] = 'test-secret'

from app import llm
from app.routers import query


def main():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE documents(id INTEGER,filename TEXT,status TEXT,lifecycle TEXT);
        CREATE TABLE chunks(id INTEGER,document_id INTEGER,seq INTEGER,page INTEGER,paragraph INTEGER,content TEXT);
        INSERT INTO documents VALUES(1,'Unit 测试.xlsx','ready','active'),
            (2,'私有.xlsx','ready','active'),(3,'历史.xlsx','ready','archived'),
            (4,'其他.xlsx','ready','active'),(5,'处理中.xlsx','processing','active');
    """)
    overlap = 'Received S7F8.\n<L[2]\n <U1[1] 2>\n'
    anchor = '工作表《SECS》第 12 行：C12=开启远程模式；E12=发送指令\nCMD REMOTE；G12=' + overlap
    tail = overlap + ' <A "Operation denied.">\n>'
    rows = [(1,1,0,None,12,anchor), (2,1,1,None,12,tail),
            (3,1,2,None,12,'工作表《HEX》第 12 行：E12=不同协议'),
            (4,1,3,None,13,'工作表《SECS》第 13 行：E13=另一个动作')]
    rows += [(i+3,i,0,None,12,anchor) for i in (2,3,4,5)]
    db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?)', rows)
    q = '《Unit 测试.xlsx》的SECS第12行发送什么，实际回复是什么？'
    with patch.object(query.embedding_service, 'embed_query', side_effect=AssertionError('literal address should not embed')):
        sources, _, reason = query.retrieve_sources(db, q, {1}, 3)
        assert reason is None and [s['chunk_id'] for s in sources] == [1,2], sources
        assert sources[0]['content'] == anchor and sources[1]['content'] == tail
        assert sources[1]['continuation_of'] == 1
        for question, scope in [(q,set()), (q,{4}), (q.replace('SECS','MISSING'),{1}),
                                (q.replace('12行','99行'),{1}),
                                (q.replace('Unit 测试','私有'),{1}),
                                (q.replace('Unit 测试','历史'),{3}),
                                (q.replace('Unit 测试','处理中'),{5})]:
            assert query.retrieve_sources(db, question, scope, 3)[0] == [], (question,scope)
    assert query._explicit_table_row_sources(db, q.replace('的SECS','的工作表《SECS》'), {1}, 3)[0]['chunk_id'] == 1
    assert query._explicit_table_row_sources(db, '不要用' + q, {1}, 3) is None
    assert query._explicit_table_row_sources(db, q.replace('12行','12行和第13行'), {1}, 3) is None
    assert query._explicit_table_row_sources(db, q.replace('12行','12行、13行'), {1}, 3) is None
    # A paragraph number alone, another worksheet, or a non-overlapping chunk cannot prove continuation.
    db.execute('UPDATE chunks SET content=? WHERE id=2', ('unrelated same-paragraph content with no overlap',))
    assert len(query._attach_table_row_continuations(db, sources[:1])) == 1
    db.execute('UPDATE chunks SET content=? WHERE id=2', (tail + '\n工作表《HEX》第 12 行：E12=别的记录',))
    assert len(query._attach_table_row_continuations(db, sources[:1])) == 1
    db.close()

    test_source = dict(sources[0], table_headers={'SECS': {'columns': {'C':'测试项','E':'测试步骤','G':'期望结果'}}})
    question = '开启远程模式要求发送什么指令？'
    # A log in a misleading expected-result column must not be reduced to a command-only shortcut.
    assert llm._test_command_projection(question, [test_source]) is None
    denied = dict(test_source, content=anchor + ' <A[17] "Operation denied.">\n>')
    projected = llm._test_command_projection(question, [denied])
    assert '该次测试记录发送：`CMD REMOTE`。[1]' in projected
    assert '`Operation denied.`。[1]' in projected
    assert '不能推断其他状态下的结果' in projected
    assert llm._test_command_projection(question, [denied, denied]) is None, 'ambiguous records must not be merged'
    sent_text = dict(denied, content=denied['content'].replace('Received S7F8.', 'Send S7F7.'))
    assert llm._test_command_projection(question, [sent_text]) is None, 'a sent string is not a received result'
    positive = dict(test_source, content=anchor.split('；G12=')[0])
    assert llm._test_command_projection(question, [positive]) == '测试记录中的发送指令：CMD REMOTE。[1]'
    assert positive['content'].endswith('CMD REMOTE')
    mode_question = 'Unit 用 SECS 指令切换远程模式应发送什么？'
    mode_answer = llm._test_command_projection(mode_question, [denied, positive])
    assert '`CMD REMOTE`。[1][2]' in mode_answer
    assert '不能确认正常操作的适用条件' in mode_answer
    for changed in [dict(positive, filename='Unit2 测试.xlsx'),
                    dict(positive, content=positive['content'].replace('远程模式','本地模式')),
                    dict(positive, content=positive['content'].replace('《SECS》','《HEX》'))]:
        assert llm._mode_test_input_projection(mode_question, [changed]) is None
    assert llm._mode_test_input_projection(mode_question, [positive,dict(positive,content=positive['content'].replace('CMD REMOTE','CMD LOCAL'))]) is None
    print('negative test record PASS')


if __name__ == '__main__':
    main()
