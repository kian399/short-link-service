"""locust 压测场景：只打跳转接口，统计 302 的比例与 P99。

用法：
    uv run locust -f bench/loadtest.py --headless -u 200 -r 20 -t 3m --host http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, events, task

CODES: list[str] = []


@events.init.add_listener
def _load_codes(environment, **kwargs):
    path = os.getenv("BENCH_CODES", "bench/codes.txt")
    with open(path, encoding="utf-8") as fh:
        CODES.extend(line.strip() for line in fh if line.strip())
    random.shuffle(CODES)
    print(f"loaded {len(CODES)} codes from {path}")


class RedirectUser(HttpUser):
    wait_time = between(0, 0.001)

    @task
    def redirect(self):
        if not CODES:
            return
        code = random.choice(CODES)
        with self.client.get(
            f"/{code}",
            name="/{code}",
            allow_redirects=False,
            catch_response=True,
        ) as resp:
            if resp.status_code != 302:
                resp.failure(f"unexpected status {resp.status_code}")
