"""进程内 TTL + LRU 缓存（L1）。

为什么自己写而不引 cachetools：
- 只需要 get/set/delete + 容量上限，40 行足够；
- 同步接口跑在 FastAPI 的线程池里，必须显式加锁，自己写能讲清锁的边界；
- 值是不可变的 dict（url/expires_at/miss 标记），不需要深拷贝。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from app.config import settings


class TTLCache:
    def __init__(self, maxsize: int = 10000, ttl: float = 30.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if expires_at <= now:
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: object, ttl: float | None = None) -> None:
        ttl = self.ttl if ttl is None else ttl
        expires_at = time.monotonic() + ttl
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)


# 进程级单例：所有请求线程共享
l1_cache = TTLCache(maxsize=settings.L1_CACHE_SIZE, ttl=settings.L1_CACHE_TTL)
