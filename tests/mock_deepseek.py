"""本地 Mock DeepSeek（OpenAI 兼容 /chat/completions），仅供测试。

启动：uvicorn tests.mock_deepseek:app --host 127.0.0.1 --port 8099
（需先激活 venv；tests 目录要能被 import，从 rag 根目录运行即可）

在用户消息内容中放入触发指令可模拟异常（指令会被忽略，不进入答案）：
  [[mock:sleep:2]]   延迟 2 秒（配合短 DEEPSEEK_TIMEOUT_S 测超时）
  [[mock:http500]]   返回 500（模拟上游故障）
  [[mock:http429]]   返回 429（模拟限流）
  [[mock:http401]]   返回 401（模拟 Key 无效）
  [[mock:http402]]   返回 402（模拟无余额）
"""
from __future__ import annotations

import asyncio
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="mock-deepseek")

MOCK_ANSWER = (
    "这是 Mock 模型返回的模拟回答。根据检索片段，《局域网运维管理规定》[1] 明确"
    "“员工应在工作日 9:00 前完成打卡”，《出差报销细则》[2] 规定“市内交通据实报销、"
    "出差住宿每日上限 400 元”。（测试环境，非真实模型输出）"
)


@app.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    user_content = ""
    for m in body.get("messages") or []:
        if m.get("role") == "user":
            user_content = m.get("content") or ""
            break

    if "[[mock:http500]]" in user_content:
        return JSONResponse(status_code=500, content={"error": {"message": "mock upstream boom"}})
    if "[[mock:http429]]" in user_content:
        return JSONResponse(status_code=429, content={"error": {"message": "mock rate limited"}})
    if "[[mock:http401]]" in user_content:
        return JSONResponse(status_code=401, content={"error": {"message": "mock invalid key"}})
    if "[[mock:http402]]" in user_content:
        return JSONResponse(status_code=402, content={"error": {"message": "mock no balance"}})

    required = re.search(r"\[\[mock:require-history:(.+?)\]\]", user_content)
    if required:
        history_text = user_content.split("当前问题：", 1)[0]
        if required.group(1) not in history_text:
            return JSONResponse(status_code=400, content={"error": {"message": "required history missing"}})

    m = re.search(r"\[\[mock:sleep:([0-9.]+)\]\]", user_content)
    delay = float(m.group(1)) if m else 0.0
    if delay:
        await asyncio.sleep(delay)

    return {
        "id": "chatcmpl-mock-1",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model") or "mock",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": MOCK_ANSWER},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 123, "completion_tokens": 87, "total_tokens": 210},
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
