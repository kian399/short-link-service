"""预热热点码：把 hot_codes.txt 全部打一遍。

目的：让 L1 和 Redis 提前装好热点数据，避免压测前 30 秒全是冷启动，
把「缓存命中率」的前半段拉低、污染整组数据。

用法：
    uv run python bench/prewarm.py                      # 默认 http://127.0.0.1:8000
    uv run python bench/prewarm.py http://127.0.0.1:8001
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def main(host: str, hot_file: str = "bench/hot_codes.txt", concurrency: int = 200) -> None:
    path = Path(hot_file)
    if not path.is_absolute():
        path = ROOT / path
    codes = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not codes:
        raise SystemExit(f"{path} 是空的，先跑 bench/seed.py")

    ok = 0
    async with httpx.AsyncClient(base_url=host, timeout=10.0, follow_redirects=False) as client:
        for i in range(0, len(codes), concurrency):
            batch = codes[i : i + concurrency]
            results = await asyncio.gather(
                *(client.get(f"/{code}") for code in batch), return_exceptions=True
            )
            ok += sum(1 for r in results if getattr(r, "status_code", 0) == 302)

    print(f"prewarmed {ok}/{len(codes)} hot codes against {host}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    asyncio.run(main(target))
