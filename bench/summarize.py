"""把五组压测的 CSV / 指标快照汇成一张 markdown 表。

用法：
    uv run python bench/summarize.py          # 读 bench/out/{A..E}_stats.csv

设计意图：报告里的数字必须能一条命令复现，不能靠人肉从五个 CSV 里抄。
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench" / "out"

LABELS = {
    "A": "A 纯 DB",
    "B": "B +Redis",
    "C": "C +L1",
    "D": "D +singleflight",
    "E": "E +埋点",
}


def read_agg(group: str) -> dict | None:
    path = OUT / f"{group}_stats.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("Name") == "Aggregated":
                return row
    return None


def read_metrics(group: str) -> dict[str, float]:
    path = OUT / f"{group}_metrics.txt"
    out: dict[str, float] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        head, value = parts
        try:
            out[head] = float(value)
        except ValueError:
            continue
    return out


def pick(metrics: dict[str, float], sample: str, **labels: str) -> float:
    """按样本名 + 标签求和，标签顺序不敏感（Prometheus 输出里的顺序不保证）。"""
    total = 0.0
    for head, value in metrics.items():
        if not head.startswith(sample + "{"):
            continue
        if all(f'{k}="{v}"' in head for k, v in labels.items()):
            total += value
    return total


def main() -> None:
    print(
        "| 组 | 请求数 | QPS | P50(ms) | P95(ms) | P99(ms) | 错误数 | L1 命中率 | Redis 命中次数 |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for g, label in LABELS.items():
        row = read_agg(g)
        if row is None:
            print(f"| {label} | — | — | — | — | — | — | — | — |")
            continue
        m = read_metrics(g)
        hits = pick(m, "cache_lookups_total", layer="l1", result="hit")
        miss = pick(m, "cache_lookups_total", layer="l1", result="miss")
        l1_rate = f"{hits / (hits + miss):.1%}" if hits + miss else "—"
        redis_hits = pick(m, "cache_lookups_total", layer="l2", result="hit")
        print(
            f"| {label} | {row['Request Count']} | {float(row['Requests/s']):.0f} | "
            f"{row['50%']} | {row['95%']} | {row['99%']} | {row['Failure Count']} | "
            f"{l1_rate} | {int(redis_hits)} |"
        )


if __name__ == "__main__":
    main()
