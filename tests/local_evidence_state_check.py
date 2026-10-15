"""Offline ON/OFF-context opt-out checks; no app config, model or database import."""
import ast
import copy
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / "app/llm.py").read_text(encoding="utf-8"))
names = {"_question_local_evidence", "_build_user_content", "_bounded_ui_projection"}
# Find the existing projection caller rather than loading application/runtime configuration.
projection = next(n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name != "_question_local_evidence" and n.name != "_build_user_content"
                  and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                          and c.func.id == "_question_local_evidence" for c in ast.walk(n)))
names.add(projection)
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
space = dict(re=re, Path=Path)
exec(compile(ast.Module(body=nodes,type_ignores=[]),"pure-local-hint","exec"),space)
hint = space["_question_local_evidence"]
build = space["_build_user_content"]


class StateHintChecks(unittest.TestCase):
    def test_state_contexts_fall_back_to_full_source(self):
        cases = [
            ("X_REQ ON何时触发？", "将X_REQ信号OFF。"),
            ("X_REQ OFF何时触发？", "X_REQ ON表示准备完成。"),
            ("X_REQ ON何时触发？", "X_REQ ON表示准备完成。"),
            ("X_REQ on何时触发？", "X_REQ off表示退出。"),
            ("X_REQ ON/OFF有什么区别？", "X_REQ ON表示准备完成。"),
            ("X_REQ ON何时触发？", "X_REQ\nON表示准备完成。"),
            ("X_REQ ON何时触发？", "X_REQ ON在完成准备且\n离开干涉区时发出。"),
            ("X_REQ ON何时触发？", "离开区域后，\n将X_REQ信号ON。"),
            ("X_REQ ON何时触发？", "X_REQ ON并不表示准备已经完成。"),
            ("X_REQ ON何时触发？", "Y_REQ ON表示准备完成；X_REQ OFF表示退出。"),
            ("X_REQ ON何时触发？", "X_REQ ON表示准备完成。\nX_REQ OFF表示退出。"),
            ("X_REQ是什么意思？", "X_REQ OFF表示退出。"),
            ("X_REQ是什么意思？", "X_REQ表示请求。\n若ON，则仍需检查条件。"),
            ("X_REQ是什么意思？", "X_REQ depends on the interlock."),
        ]
        for question, content in cases:
            with self.subTest(question=question,content=content):
                self.assertIsNone(hint(question,content))
                sources=[dict(filename="synthetic.pdf",page=2,content=content)]
                original=copy.deepcopy(sources)
                prompt=build(question,sources)
                self.assertNotIn("问题相关局部证据：",prompt)
                self.assertIn(content,prompt)
                self.assertIn("[1] 文件《synthetic.pdf》",prompt)
                self.assertEqual(sources,original)

    def test_non_state_exact_unique_behavior_retained(self):
        question="Z9-Ack操作前需要什么？"
        sentence="Z9-Ack清除告警。（需先确认锁定）"
        self.assertEqual(hint(question,"前文。\n"+sentence+"\n后文。"),sentence)
        self.assertIsNone(hint(question,sentence+"\nZ9-Ack另有条件。"))
        self.assertIsNone(hint(question,"Z9-Ack2属于别的命令。"))
        self.assertIsNone(hint(question,"Z9-Ack"+"长"*400))
        for identifier in ("POWER_ON", "ONCE_REQ", "OFFSET_REQ", "X_ONLINE"):
            self.assertEqual(hint(identifier+"是什么意思？",identifier+"表示字段。"),identifier+"表示字段。")

    def test_projection_caller_is_conservative(self):
        question="OMEGA PRO 教导器清除输出告警的 F7-Approve 操作前，手册要求先做什么？"
        source=dict(filename="OMEGA PRO调试手册.docx",content="F7-Approve可以清除当前输出告警。（需先使用Confirm确认告警）")
        project=space[projection]
        self.assertEqual(project(question,[source]),"需先使用Confirm确认告警。[1]")
        self.assertIsNone(project(question,[dict(source,content=source["content"]+"\nREADY ON还有条件。")]))


if __name__ == "__main__":
    unittest.main()
