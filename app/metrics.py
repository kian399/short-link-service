"""进程内 Prometheus 指标（零第三方依赖）。

取舍：
- 只有 4 类指标，引 prometheus_client 会给镜像多一个运行时依赖；
- 自己实现计数器/直方图，面试能讲清标签与桶的原理；
- 只做单进程聚合，多 worker 各算各的（压测本来就是单进程，够用）。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
)

_lock = threading.Lock()
_counters: dict[tuple[str, tuple], float] = defaultdict(float)
_gauges: dict[tuple[str, tuple], float] = {}
_histograms: dict[tuple[str, tuple], _Histogram] = {}

_HELP = {
    "http_requests_total": "HTTP 请求数（按 method/path/status）",
    "http_request_duration_seconds": "HTTP 请求耗时（秒）",
    "cache_lookups_total": "缓存查询次数（按 layer/result）",
    "singleflight_total": "singleflight 调用次数（leader/shared）",
    "db_pool_checked_out": "SQLAlchemy 已借出连接数",
    "db_pool_size": "SQLAlchemy 连接池大小",
    "l1_cache_size": "L1 缓存条目数",
    "l1_cache_hits_total": "L1 命中次数",
    "l1_cache_misses_total": "L1 未命中次数",
}


def _key(name: str, labels: dict | None) -> tuple[str, tuple]:
    return name, tuple(sorted((labels or {}).items()))


class _Histogram:
    __slots__ = ("buckets", "counts", "count", "total")

    def __init__(self, buckets: Iterable[float]) -> None:
        self.buckets = tuple(buckets)
        self.counts = [0] * len(self.buckets)
        self.count = 0
        self.total = 0.0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        # 只累加到第一个命中的桶；渲染时再做累积，避免重复计数
        for i, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[i] += 1
                break


def inc(name: str, labels: dict | None = None, value: float = 1.0) -> None:
    key = _key(name, labels)
    with _lock:
        _counters[key] += value


def set_gauge(name: str, value: float, labels: dict | None = None) -> None:
    key = _key(name, labels)
    with _lock:
        _gauges[key] = value


def observe(
    name: str,
    value: float,
    labels: dict | None = None,
    buckets: Iterable[float] = DEFAULT_BUCKETS,
) -> None:
    key = _key(name, labels)
    with _lock:
        hist = _histograms.get(key)
        if hist is None:
            hist = _Histogram(buckets)
            _histograms[key] = hist
        hist.observe(value)


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(pairs: tuple, extra: str | None = None) -> str:
    parts = [f'{k}="{_escape(v)}"' for k, v in pairs]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def render() -> str:
    with _lock:
        counters = dict(_counters)
        gauges = dict(_gauges)
        hists = {k: (h.buckets, list(h.counts), h.count, h.total) for k, h in _histograms.items()}

    hist_names = {k[0] for k in hists}
    gauge_names = {k[0] for k in gauges}
    names = sorted({k[0] for k in counters} | gauge_names | hist_names)

    lines: list[str] = []
    for name in names:
        lines.append(f"# HELP {name} {_HELP.get(name, name)}")
        kind = (
            "histogram" if name in hist_names else ("gauge" if name in gauge_names else "counter")
        )
        lines.append(f"# TYPE {name} {kind}")

        for (n, labels), value in sorted(counters.items()):
            if n == name:
                lines.append(f"{name}{_labels(labels)} {value}")
        for (n, labels), value in sorted(gauges.items()):
            if n == name:
                lines.append(f"{name}{_labels(labels)} {value}")
        for (n, labels), (buckets, counts, count, total) in sorted(hists.items()):
            if n != name:
                continue
            cumulative = 0
            for bound, c in zip(buckets, counts, strict=False):
                cumulative += c
                le = f'le="{bound}"'
                lines.append(f"{name}_bucket{_labels(labels, le)} {cumulative}")
            inf_le = 'le="+Inf"'
            lines.append(f"{name}_bucket{_labels(labels, inf_le)} {count}")
            lines.append(f"{name}_sum{_labels(labels)} {total}")
            lines.append(f"{name}_count{_labels(labels)} {count}")

    return "\n".join(lines) + "\n"


def reset() -> None:
    """测试用：清空全部指标。"""
    with _lock:
        _counters.clear()
        _gauges.clear()
        _histograms.clear()
