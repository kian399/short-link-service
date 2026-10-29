"""singleflight：同一 key 的并发回源只放一个请求进去，其余等结果。

用来防缓存击穿：某个热点短码缓存刚好失效时，1000 个并发请求
不应该一起打到 PostgreSQL，而应该只有 1 个去查，其余复用结果。
"""

from __future__ import annotations

import threading

from app import metrics


class _Call:
    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value = None
        self.error: BaseException | None = None


class SingleFlight:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, _Call] = {}
        self.shared = 0

    def do(self, key: str, fn):
        with self._lock:
            call = self._calls.get(key)
            leader = call is None
            if leader:
                call = _Call()
                self._calls[key] = call
            else:
                self.shared += 1

        if not leader:
            call.event.wait()
            metrics.inc("singleflight_total", {"result": "shared"})
            if call.error is not None:
                raise call.error
            return call.value

        try:
            call.value = fn()
        except BaseException as exc:  # noqa: BLE001 需要把异常原样传给等待者
            call.error = exc
            metrics.inc("singleflight_total", {"result": "leader_error"})
            raise
        else:
            metrics.inc("singleflight_total", {"result": "leader_ok"})
            return call.value
        finally:
            with self._lock:
                self._calls.pop(key, None)
            call.event.set()


single_flight = SingleFlight()
