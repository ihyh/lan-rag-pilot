"""全局模型请求并发闸门（跨线程）。"""
from __future__ import annotations

from .config import settings
from .ratelimit import ConcurrencyGate

llm_gate = ConcurrencyGate(settings.max_concurrent_llm)
