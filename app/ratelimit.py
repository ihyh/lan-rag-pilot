"""进程内滑动窗口限流 + 跨线程并发闸门（线程安全）。

设计前提：单 uvicorn worker。若未来迁移多进程，需换成 Redis 等共享存储。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class SlidingWindowLimiter:
    """滑动窗口限流器。limit/window 可在调用时覆盖（支持运行时配置调整）。"""

    limit: int
    window_seconds: float = 60.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _hits: dict = field(default_factory=lambda: defaultdict(deque))

    def allow(self, key: str, limit: int | None = None, window: float | None = None) -> tuple[bool, float]:
        limit = self.limit if limit is None else limit
        window = self.window_seconds if window is None else window
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            cutoff = now - window
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= limit:
                retry = max(0.0, q[0] + window - now)
                return False, retry
            q.append(now)
            if len(self._hits) > 8192:  # 惰性清理，防止 key 无限膨胀
                dead = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
                for k in dead:
                    self._hits.pop(k, None)
            return True, 0.0


class ConcurrencyGate:
    """跨线程并发上限（替代 asyncio.Semaphore，兼容同步线程池端点）。"""

    def __init__(self, maximum: int) -> None:
        self._max = max(1, maximum)
        self._active = 0
        self._cv = threading.Condition()

    @property
    def maximum(self) -> int:
        with self._cv:
            return self._max

    def set_max(self, maximum: int) -> None:
        with self._cv:
            self._max = max(1, maximum)
            self._cv.notify_all()

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._active >= self._max:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(timeout=remaining)
            self._active += 1
            return True

    def release(self) -> None:
        with self._cv:
            self._active = max(0, self._active - 1)
            self._cv.notify()
