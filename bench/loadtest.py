"""locust 压测场景：80% 请求打热点码，20% 打长尾，模拟真实访问分布。

用法：
    uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m \
        --host http://127.0.0.1:8000 --csv bench/out/C_l1l2

环境变量：
    BENCH_HOT_RATIO  热点请求占比，默认 0.8
    BENCH_CODES      全量码文件，默认 bench/codes.txt
    BENCH_HOT_CODES  热点码文件，默认 bench/hot_codes.txt

为什么不是均匀随机：
均匀打 10 万条、L1 只有 1 万条时，命中率上限 10%，缓存收益被容量稀释；
80/20 的热点分布更接近真实流量，能把 L1 的价值打出来。
"""

from __future__ import annotations

import os
import random
from pathlib import Path

from locust import HttpUser, between, events, task

ALL_CODES: list[str] = []
HOT_CODES: list[str] = []

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> list[str]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


@events.init.add_listener
def _load_codes(environment, **kwargs):
    all_path = os.getenv("BENCH_CODES", "bench/codes.txt")
    hot_path = os.getenv("BENCH_HOT_CODES", "bench/hot_codes.txt")

    ALL_CODES.extend(_load(all_path))
    HOT_CODES.extend(_load(hot_path))

    if not ALL_CODES:
        raise SystemExit(
            f"没有找到 {all_path}，请先执行: uv run python bench/seed.py 100000 5000"
        )
    if not HOT_CODES:
        # 没生成热点文件时退化成前 5% 当热点
        HOT_CODES.extend(ALL_CODES[: max(1, len(ALL_CODES) // 20)])

    random.shuffle(ALL_CODES)
    random.shuffle(HOT_CODES)
    print(f"loaded {len(ALL_CODES)} codes, {len(HOT_CODES)} hot")


class RedirectUser(HttpUser):
    wait_time = between(0, 0.001)

    @task
    def redirect(self):
        if not ALL_CODES:
            return
        hot_ratio = float(os.getenv("BENCH_HOT_RATIO", "0.8"))
        pool = HOT_CODES if (HOT_CODES and random.random() < hot_ratio) else ALL_CODES
        code = random.choice(pool)
        with self.client.get(
            f"/{code}",
            name="/{code}",
            allow_redirects=False,
            catch_response=True,
        ) as resp:
            if resp.status_code != 302:
                resp.failure(f"unexpected status {resp.status_code}")