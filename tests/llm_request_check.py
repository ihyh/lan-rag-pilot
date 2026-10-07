"""验证 Qwen3 关闭思考时仍保留当前引用与多轮上下文，不访问真实模型。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DEEPSEEK_API_KEY"] = "mock-key"
os.environ["RAG_SECRET_KEY"] = "test-secret"
os.environ["RAG_ROOT_PASSWORD"] = "test-password"

from app import llm


def main() -> None:
    raw = ("C9=unlocated tail\n工作表《HEX》第 2 行：C2=move；F2=22；L2=发送指令22\n"
           "工作表《另一表》第 2 行：C2=条件；F2=42")
    table = {"filename": "synthetic.xlsx", "paragraph": 2, "content": raw,
             "table_headers": {"HEX": {"chunk_id": 1, "columns": {"C": "Type", "F": "Input", "L": "测试步骤"}},
                               "另一表": {"chunk_id": 2, "columns": {"C": "Precondition", "F": "Reply"}}}}
    annotated = llm._build_user_content("系统复位的指令？", [table])
    assert "C2(Type)=move；F2(Input)=22；L2(测试步骤)=发送指令22" in annotated
    assert "C2(Precondition)=条件；F2(Reply)=42" in annotated, "headers must be sheet-specific"
    assert "C9=unlocated tail" in annotated and "C9(Type)" not in annotated
    assert "[1] 文件《synthetic.xlsx》" in annotated and "[2] 文件" not in annotated
    assert table["content"] == raw, "annotation must not rewrite original citation content"
    assert "C2=move；F2=22" in llm._build_user_content("指令？", [dict(table, table_headers={})]), \
        "missing headers must preserve raw cells rather than guessing their roles"
    log_cells = dict(table, content="工作表《HEX》第 2 行：L2=日志\nF999=错误行号\nF2=日志引用\n说明：F2=引用；F2=22")
    log_prompt = llm._build_user_content("指令？", [log_cells])
    assert "F999(Input)" not in log_prompt and "\nF2(Input)" not in log_prompt
    assert "说明：F2=引用；F2(Input)=22" in log_prompt, "only real same-row cell boundaries get labels"
    overlong = dict(table, table_headers={"HEX": {"columns": {"F": "长" * 41}}})
    assert "F2=" in llm._build_user_content("指令？", [overlong]), "oversized labels must stay unused"
    repeated = dict(table, content="\n".join(f"工作表《HEX》第 {row} 行：F{row}=值" for row in range(1, 31)),
                    table_headers={"HEX": {"columns": {"F": "列" * 40}}})
    annotated_repeated = llm._build_user_content("指令？", [repeated])
    plain_repeated = llm._build_user_content("指令？", [dict(repeated, table_headers={})])
    assert len(annotated_repeated) - len(plain_repeated) <= 400, "annotation amplification must be bounded"

    focus_source = {
        "filename": "generic-guide.pdf",
        "page": 9,
        "content": "前文介绍参数浏览和页面切换。\n"
                   "Z9-Ack可以清除当前告警。（需先使用Confirm确认告警来解除键盘锁定）\n"
                   "后文介绍速度调整和其它按键。",
    }
    focused = llm._build_user_content("执行 Z9-Ack 前需要先做什么？", [focus_source])
    assert "问题相关局部证据：Z9-Ack可以清除当前告警。（需先使用Confirm确认告警来解除键盘锁定）" in focused, \
        "唯一结构化标识符应把同一短句提升到长片段前"
    repeated_focus = dict(focus_source, content=focus_source["content"] + "\nZ9-Ack也可用于另一类告警。")
    assert "问题相关局部证据：" not in llm._build_user_content(
        "执行 Z9-Ack 前需要先做什么？", [repeated_focus]
    ), "标识符多处出现时不得猜选局部证据"
    near_match = dict(focus_source, content="Z9-Ack2属于另一条指令。")
    assert "问题相关局部证据：" not in llm._build_user_content(
        "执行 Z9-Ack 前需要先做什么？", [near_match]
    ), "结构化标识符只能完整匹配，不能命中更长标识符的子串"

    action_source = {
        "filename": "synthetic.xlsx",
        "paragraph": 60,
        "content": ("工作表《TestCase》第 60 行：B60=翻转输出\n"
                    "（测试：闪烁后关闭，发送2次）；F60=05 35；L60=发送指令\n05 35"),
        "table_headers": {"TestCase": {"columns": {"B": "测试项", "F": "Input", "L": "测试步骤"}}},
    }
    action_question = "翻转输出的测试动作是什么？"
    action_constraints = llm._explicit_test_action_constraints(action_question, [action_source])
    assert action_constraints == ["05 35", "闪烁后关闭", "发送2次"]
    assert not llm._explicit_test_action_constraints("翻转输出的指令是什么？", [action_source])
    assert not llm._explicit_test_action_constraints(
        action_question, [dict(action_source, table_headers={})]
    ), "untyped source text must not activate source projection"

    structure_source = {
        "filename": "manual.pdf",
        "page": 14,
        "content": ("report\noption\nStructure:\nEERA\nXX(CR)(LF)\nW\nhere:\n"
                    "XX\ncan\nbe\neither\nOK\nor\nDENIED"),
    }
    structure_question = "ASCII 协议的 report option 结构是什么？"
    condition_source = {
        "filename": "cases.xlsx",
        "paragraph": 3,
        "content": ("工作表《Cases》第 3 行：C3=关闭CLOSE互锁指令功能；D3=1.PLUS上电\n"
                    "2.目前是AUTO模式\n3.无警告；E3=HCS DISABLE CLOSE\n"
                    "工作表《Cases》第 4 行：C4=关闭OPEN互锁指令功能；D4=另一组条件"),
        "table_headers": {"Cases": {"columns": {"C": "测试项", "D": "前置条件", "E": "测试步骤-输入"}}},
    }
    condition_question = "关闭 CLOSE 互锁指令功能的测试前置条件有哪些？"

    command_question = "PLUS PRO TCP-HEX 中，读取最后一次 Mapping 结果的指令 ID 和回复 ID 是什么？"
    command_sources = [
        {"filename": "manual.pdf", "content": "Mapping sensor 数据的 offset 值。"},
        {"filename": "manual.pdf", "content": "ID=0x01 表示请求状态，ID=0x61 表示回复状态。"},
        {"filename": "manual.pdf", "content":
         "0\nx\n0C\n读取最后一次\nmapping\n结果\n无\n0x\n73\n"
         "0x0D\n读取\nmapping\n详细数据\n无\n0x84"},
    ]
    status_question = "PLUS PRO GUI 中 Standby 状态表示什么，操作前需要什么？"
    status_sources = [
        {"filename": "gui.pdf", "content": "Power On：系统已上电。"},
        {"filename": "gui.pdf", "content": "Alarm：系统存在告警。"},
        {"filename": "gui.pdf", "content": "iv. Standby：表示系统处于未就绪状态，需要执行复位后才能操作。"},
    ]
    control_question = "ControlMode 显示值 true 和 false 分别对应哪种模式？"
    control_sources = [{"filename": "gui.pdf", "content":
                        "ControlMode：true 表示 Local 模式，false 表示 Remote 模式。"}]
    exit_question = "教导器退出到 Exit 状态后，网页和串口分别可切换到什么模式？"
    exit_sources = [{"filename": "gui.pdf", "content":
                     "Exit状态：网页可切换到local模式，串口通讯可切换到Remote模式；"}]
    operations_question = "PLUS PRO GUI 的 Local 模式可用于哪些操作？"
    operations_sources = [{"filename": "gui.pdf", "content":
                           "Local 模式可以在 GUI 端控制和查看状态。菜单栏："
                           "a) Home，主页面 b) Monitors，查看系统状态 "
                           "c) Parameters，查看以及编辑参数 d) Control，对单个轴进行控制 "
                           "e) Script，编辑 macro f) System，对底层系统的控制 REMOTE 模式。"}]

    assert llm._source_projection(command_question, command_sources) == \
        "指令 ID 为 0x0C，回复 ID 为 0x73。[3]"
    assert llm._source_projection(status_question, status_sources) == \
        "iv. Standby：表示系统处于未就绪状态，需要执行复位后才能操作。[3]"
    assert llm._source_projection(control_question, control_sources) == \
        "ControlMode：true 表示 Local 模式，false 表示 Remote 模式。[1]"
    assert llm._source_projection(exit_question, exit_sources) == \
        "Exit状态：网页可切换到local模式，串口通讯可切换到Remote模式；[1]"
    assert llm._source_projection(operations_question, operations_sources) == \
        ("Local 模式可用于：Home：主页面；Monitors：查看系统状态；Parameters：查看以及编辑参数；"
         "Control：对单个轴进行控制；Script：编辑 macro；System：对底层系统的控制。[1]")
    assert llm._source_projection(
        "ALPHA 中，读取最后一次 Snapshot 结果的指令 ID 和回复 ID 是什么？",
        [{"filename": "changed.pdf", "content": "0x11 读取最后一次 Snapshot 结果 无 0x91；"}],
    ) == "指令 ID 为 0x11，回复 ID 为 0x91。[1]"
    assert llm._source_projection(
        "ALPHA 中，读取最后一次 Snapshot 结果的指令 ID 和回复 ID 是什么？",
        [
            {"filename": "table.pdf", "content": "0x11 读取最后一次 Snapshot 结果 无 0x91；"},
            {"filename": "detail.pdf", "content":
             "0x11 控制将返回最后一次 Snapshot 的结果（回复的消息 ID=0x91）。"},
            {"filename": "table.pdf", "content": "0x12 读取 Snapshot 详细数据 无 0x92；"},
        ],
    ) == "指令 ID 为 0x11，回复 ID 为 0x91。[1]"
    assert llm._source_projection(
        "设备的 ReadyMode 显示值对应哪两种模式？",
        [{"filename": "changed.pdf", "content":
          "ReadyMode：yes 表示 Service 模式，no 表示 User 模式。"}],
    ) == "ReadyMode：yes 表示 Service 模式，no 表示 User 模式。[1]"
    assert llm._source_projection(
        "ALPHA 中，读取最后一次 Snapshot 结果的指令 ID 和回复 ID 是什么？",
        [{"filename": "ambiguous.pdf", "content":
          "0x11 读取最后一次 Snapshot 结果 无 0x91；"
          "0x12 读取最后一次 Snapshot 结果 无 0x92；"}],
    ) is None
    assert llm._source_projection(
        "设备的 ReadyMode 显示值对应哪两种模式？",
        [{"filename": "ambiguous.pdf", "content":
          "ReadyMode：yes 表示 Service 模式，no 表示 User 模式。"
          "ReadyMode：1 表示 A 模式，0 表示 B 模式。"}],
    ) is None
    assert llm._source_projection(
        operations_question,
        [{"filename": "ambiguous.pdf", "content":
          "Local 模式 a) Monitors，查看状态 b) Control，控制轴 REMOTE 模式。"
          "Local 模式 a) Parameters，编辑参数 b) Script，编辑 macro REMOTE 模式。"}],
    ) is None
    assert llm._source_projection(
        "Snapshot 的指令 ID 和回复 ID 是什么？",
        [{"filename": "near.pdf", "content": "0x11 SnapshotPlus 无 0x91；"}],
    ) is None

    original_client = httpx.Client
    sources = [{"filename": "synthetic.txt", "page": 1, "content": "服务窗口为09:00至17:00。"}]
    history = [{"question": "服务在哪里？", "answer": "测试服务中心。"}]
    for model in ("qwen3:1.7b", "qwen3:4b", "qwen3", "mock-model", "deepseek-v4-flash"):
        config = SimpleNamespace(
            deepseek_api_key="mock-key", deepseek_base_url="http://127.0.0.1:11434/v1",
            deepseek_model=model, deepseek_timeout_s=1, llm_temperature=0.2, llm_max_tokens=1200,
            llm_trust_env_proxy=False,
        )

        def respond(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["max_tokens"] == 1200 and body["stream"] is False
            prompt = body["messages"][0]["content"]
            assert "有部分依据时回答该部分" in prompt and "全部无依据" in prompt
            assert "200字" in prompt and "每个有依据的结论后必须" in prompt
            if model in ("qwen3:1.7b", "qwen3:4b", "qwen3"):
                assert body["reasoning_effort"] == "none"
            else:
                assert "reasoning_effort" not in body
            content = body["messages"][1]["content"]
            assert "服务在哪里？" in content and "测试服务中心。" in content
            assert "几点开放？" in content and "[1] 文件《synthetic.txt》" in content
            assert "服务窗口为09:00至17:00。" in content
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "09:00至17:00。[1]"}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 12},
            })

        client = original_client(transport=httpx.MockTransport(respond), trust_env=False)
        with patch.object(llm, "settings", config), patch.object(llm.httpx, "Client", return_value=client):
            result = llm.chat("几点开放？", sources, history)
        assert result["answer"] == "09:00至17:00。[1]"
        assert result["model"] == model and result["completion_tokens"] == 12

    projection_config = SimpleNamespace(
        deepseek_api_key="   ", deepseek_base_url="http://127.0.0.1:11434/v1",
        deepseek_model="qwen3:1.7b", deepseek_timeout_s=1, llm_temperature=0.2,
        llm_max_tokens=1200, llm_trust_env_proxy=False,
    )
    projection_cases = [
        (action_question, [action_source], "测试动作：05 35；闪烁后关闭；发送2次。[1]"),
        (structure_question, [structure_source, {"filename": "other.txt", "content": "EDER"}],
         "结构：EERA XX(CR)(LF)，其中 XX 可为 OK 或 DENIED。[1]"),
        (condition_question, [condition_source, {"filename": "other.txt", "content": "LOAD"}],
         "测试前置条件：1.PLUS上电；2.目前是AUTO模式；3.无警告。[1]"),
        ("alarm option 结构是什么？", [{"filename": "other.pdf", "content":
          "alarm option Structure:\nABCD YY<CR><LF>\nWhere:\nYY can be either ACCEPT or REJECT"}],
         "结构：ABCD YY<CR><LF>，其中 YY 可为 ACCEPT 或 REJECT。[1]"),
        ("开启门互锁的测试前置条件有哪些？", [{
            "filename": "other.xlsx",
            "content": "工作表《Other》第 7 行：Q7=开启门互锁；Z7=1.设备上电\n2.门已关闭",
            "table_headers": {"Other": {"columns": {"Q": "测试项", "Z": "前置条件"}}},
        }], "测试前置条件：1.设备上电；2.门已关闭。[1]"),
        (command_question, command_sources, "指令 ID 为 0x0C，回复 ID 为 0x73。[3]"),
        (status_question, status_sources,
         "iv. Standby：表示系统处于未就绪状态，需要执行复位后才能操作。[3]"),
        (control_question, control_sources,
         "ControlMode：true 表示 Local 模式，false 表示 Remote 模式。[1]"),
        (exit_question, exit_sources,
         "Exit状态：网页可切换到local模式，串口通讯可切换到Remote模式；[1]"),
        (operations_question, operations_sources,
         "Local 模式可用于：Home：主页面；Monitors：查看系统状态；Parameters：查看以及编辑参数；"
         "Control：对单个轴进行控制；Script：编辑 macro；System：对底层系统的控制。[1]"),
    ]
    with patch.object(llm, "settings", projection_config), \
            patch.object(llm.httpx, "Client", side_effect=AssertionError("model must not be called")):
        for projection_question, projection_sources, grounded in projection_cases:
            result = llm.chat(projection_question, projection_sources)
            events = list(llm.stream_chat(projection_question, projection_sources))
            assert result == {"answer": grounded, "model": "source_projection", "latency_ms": 0,
                              "prompt_tokens": 0, "completion_tokens": 0}
            assert events == [
                {"type": "delta", "text": grounded},
                {"type": "usage", "model": "source_projection", "latency_ms": 0,
                 "prompt_tokens": 0, "completion_tokens": 0},
            ]

    fallback_requests = []

    def fallback_response(request: httpx.Request) -> httpx.Response:
        fallback_requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "fallback。[1]"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    rejected_sources = [
        [dict(structure_source, content=structure_source["content"]
              + "\nreport option Structure:\nABCD XX(CR)(LF)\nWhere:\nXX can be either YES or NO")],
        [dict(structure_source, content=structure_source["content"].replace("W\nhere:", ""))],
        [dict(condition_source, table_headers={})],
        [dict(condition_source, content=condition_source["content"]
              + "\n工作表《Cases》第 99 行：C99=关闭CLOSE互锁指令功能；D99=另一组条件")],
    ]
    def fallback_client(**_kwargs):
        return original_client(transport=httpx.MockTransport(fallback_response), trust_env=False)

    with patch.object(llm, "settings", config), patch.object(llm.httpx, "Client", side_effect=fallback_client):
        answers = [llm.chat(structure_question if index < 2 else condition_question, rejected)["answer"]
                   for index, rejected in enumerate(rejected_sources)]
    assert answers == ["fallback。[1]"] * 4 and len(fallback_requests) == 4

    mapping_questions = [
        "设备的 ReadyMode 显示值 yes 和 no 分别对应什么模式？",
        "设备的 ReadyMode 显示值对应哪两种模式？",
    ]
    for malformed_mapping in (
        "ReadyMode：yes 表示 Service 模式，YES 表示 User 模式。",
        "ReadyMode：yes 表示 Service 模式Plus，no 表示 User 模式。",
        "ReadyMode：yes 表示 Service 模式，另一个模式需查表。",
    ):
        assert llm._source_projection(mapping_questions[1], [{
            "filename": "unknown.pdf", "content": malformed_mapping,
        }]) is None, "unknown mapping formats or repeated values must use model fallback"
    complete_mapping = "yes 表示 Service 模式，no 表示 User 模式。[1]"
    for mapping_question in mapping_questions:
        for separator in ("。", "；"):
            mapping_source = {"filename": "split.pdf", "content":
                              f"ReadyMode：yes 表示 Service 模式{separator}no 表示 User 模式。"}
            assert llm._source_projection(mapping_question, [mapping_source]) is None, \
                "a split mode mapping must not project only the label's first sentence"
            mapping_requests = []

            def mapping_response(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                mapping_requests.append(body["stream"])
                user_content = body["messages"][-1]["content"]
                assert mapping_question in user_content
                assert mapping_source["content"] in user_content, \
                    "fallback must receive both original sentences without guessed joining"
                if body["stream"]:
                    event = {"choices": [{"delta": {"content": complete_mapping}}]}
                    return httpx.Response(200, content=(
                        "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
                    ).encode())
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": complete_mapping}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                })

            def mapping_client(**_kwargs):
                return original_client(transport=httpx.MockTransport(mapping_response), trust_env=False)

            with patch.object(llm, "settings", config), \
                    patch.object(llm.httpx, "Client", side_effect=mapping_client):
                result = llm.chat(mapping_question, [mapping_source])
                events = list(llm.stream_chat(mapping_question, [mapping_source]))
            assert result["answer"] == complete_mapping and result["model"] == config.deepseek_model
            assert "".join(event["text"] for event in events if event["type"] == "delta") == complete_mapping
            assert mapping_requests == [False, True], "both paths must call the fallback model"

    assert llm._normalize_answer("可在维护页调整。[1]\n根据知识库现有内容无法回答该问题。") == "可在维护页调整。[1]"
    assert llm._normalize_answer("10。[1]\n根据知识库现有内容无法回答该问题。") == "10。[1]"
    assert llm._normalize_answer("根据知识库现有内容无法回答该问题。[3]") == llm.NO_ANSWER

    # 成功状态的坏响应必须仍走稳定业务错误，不让原始解析异常逃到路由层。
    for bad in (
        {"choices": [{"message": {"content": 7}}]},
        {"choices": [{"message": {"content": "ok"}}], "usage": [1]},
        {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": "bad"}},
        {"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": "bad"}},
        b'{"choices":[{"message":{"content":"ok"}}],"usage":{"prompt_tokens":Infinity}}',
    ):
        client = original_client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=bad) if isinstance(bad, bytes)
            else httpx.Response(200, json=bad)), trust_env=False)
        with patch.object(llm, "settings", config), patch.object(llm.httpx, "Client", return_value=client):
            try:
                llm.chat("问", sources)
            except llm.LLMError as exc:
                assert exc.code == "llm_bad_response", exc.code
            else:
                raise AssertionError(f"malformed success response accepted: {bad}")

    for field, value in (("prompt_tokens", '"bad"'), ("completion_tokens", '"bad"'),
                         ("prompt_tokens", "Infinity")):
        stream = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                  + f'data: {{"choices":[],"usage":{{"{field}":{value}}}}}\n\n'.encode()
                  + b'data: [DONE]\n\n')
        client = original_client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=stream)), trust_env=False)
        with patch.object(llm, "settings", config), patch.object(llm.httpx, "Client", return_value=client):
            events = llm.stream_chat("问", sources)
            assert next(events) == {"type": "delta", "text": "ok"}
            try:
                next(events)
            except llm.LLMError as exc:
                assert exc.code == "llm_bad_response", exc.code
            else:
                raise AssertionError(f"malformed stream {field} accepted")

    for invalid in (0, False, []):
        stream = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                  + f'data: {json.dumps({"choices": [{"delta": {"content": invalid}}]})}\n\n'.encode()
                  + b'data: [DONE]\n\n')
        client = original_client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=stream)), trust_env=False)
        with patch.object(llm, "settings", config), patch.object(llm.httpx, "Client", return_value=client):
            events = llm.stream_chat("问", sources)
            assert next(events) == {"type": "delta", "text": "ok"}
            try:
                next(events)
            except llm.LLMError as exc:
                assert exc.code == "llm_bad_response", exc.code
            else:
                raise AssertionError(f"falsey non-string stream content accepted: {invalid!r}")

    # 真正用于"提问"的 chat 路径也要守住，不能只在健康探测里覆盖。
    blank_key_config = SimpleNamespace(
        deepseek_api_key="   ", deepseek_base_url="http://127.0.0.1:11434/v1",
        deepseek_model="qwen3:1.7b", deepseek_timeout_s=1, llm_temperature=0.2,
        llm_max_tokens=1200, llm_trust_env_proxy=False,
    )
    sent: list[httpx.Request] = []
    client_kwargs: dict = {}

    def bare_response(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    def spy_client(**kwargs):
        client_kwargs.update(kwargs)
        return original_client(
            transport=httpx.MockTransport(bare_response),
            trust_env=kwargs.get("trust_env", True),
        )

    # 1) 只有空白的 key 必须 fail-closed 抛 llm_auth，而不是发出 `Authorization: Bearer `。
    #    后者是非法 HTTP 头，httpcore 会在建连前抛 LocalProtocolError，报成"无法连接
    #    模型服务"，让运维去查一个本来正常的 Ollama。健康探测采用同一判断，
    #    所以这里的行为与 /api/ready 的结论必须一致。
    with patch.object(llm, "settings", blank_key_config), \
            patch.object(llm.httpx, "Client", side_effect=spy_client):
        try:
            llm.chat("问", sources, None)
        except llm.LLMError as exc:
            assert exc.code == "llm_auth", f"应报 llm_auth，实际 {exc.code}"
        else:
            raise AssertionError("只有空白的 key 应抛 llm_auth，实际却发出了请求")
    assert not sent, f"key 非法时不得发出任何请求，实际发了 {len(sent)} 次"

    # 2) 正常 key：Authorization 头正确，且必须显式不使用系统/环境代理。
    #    模型服务是本机/内网依赖，被代理接管会得到 502。
    ok_key_config = SimpleNamespace(
        deepseek_api_key="ollama", deepseek_base_url="http://127.0.0.1:11434/v1",
        deepseek_model="qwen3:1.7b", deepseek_timeout_s=1, llm_temperature=0.2,
        llm_max_tokens=1200, llm_trust_env_proxy=False,
    )
    with patch.object(llm, "settings", ok_key_config), \
            patch.object(llm.httpx, "Client", side_effect=spy_client):
        llm.chat("问", sources, None)
    assert len(sent) == 1, f"应只发出一次请求，实际 {len(sent)}"
    assert sent[0].headers.get("authorization") == "Bearer ollama", (
        f"Authorization 头不正确：{sent[0].headers.get('authorization')!r}"
    )
    assert client_kwargs.get("trust_env") is False, (
        f"模型服务客户端必须显式不使用环境代理，实际 trust_env={client_kwargs.get('trust_env')!r}"
    )

    print("LLM request check: PASS (source-only structured projection and 5 model variants)")


if __name__ == "__main__":
    main()
