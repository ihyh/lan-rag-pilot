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
    print("LLM request check: PASS (5 model variants, history and sources preserved)")


if __name__ == "__main__":
    main()
