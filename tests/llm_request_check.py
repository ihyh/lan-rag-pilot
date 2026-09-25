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
    assert llm._normalize_answer("可在维护页调整。[1]\n根据知识库现有内容无法回答该问题。") == "可在维护页调整。[1]"
    assert llm._normalize_answer("10。[1]\n根据知识库现有内容无法回答该问题。") == "10。[1]"
    assert llm._normalize_answer("根据知识库现有内容无法回答该问题。[3]") == llm.NO_ANSWER

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

    print("LLM request check: PASS (5 model variants, history and sources preserved)")


if __name__ == "__main__":
    main()
