"""造压测数据：往库里灌 N 条短链，并生成 codes.txt 与 hot_codes.txt。

用法（在项目根目录执行，脚本会自己把根目录加进 sys.path）：
    uv run python bench/seed.py 100000 5000
                                          ↑ 总数   ↑ 热点码数量（默认总数的 5%）

为什么要热点码：
真实短链访问是「长尾 + 热点」，不是均匀分布。10 万条均匀打、L1 只有 1 万条时，
命中率上限只有 10%，测不出缓存价值；让 80% 请求打 5000 个热点码，
才能得到「命中率 80%+」这种可讲的数据，同时保留长尾去压回源路径。
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

# 允许 `python bench/seed.py` 直接跑：把项目根目录放进 sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models.link import Link  # noqa: E402
from app.services.shortener import BASE62_ALPHABET  # noqa: E402

CHUNK = 5000


def _insert_codes(codes_path: Path, total: int) -> list[str]:
    codes: list[str] = []
    with SessionLocal() as db, codes_path.open("w", encoding="utf-8") as fh:
        while len(codes) < total:
            batch = []
            for _ in range(min(CHUNK, total - len(codes))):
                code = "".join(secrets.choice(BASE62_ALPHABET) for _ in range(7))
                batch.append(Link(code=code, original_url=f"https://example.com/{code}"))
                codes.append(code)
                fh.write(code + "\n")
            db.add_all(batch)
            db.commit()
            print(f"seeded {len(codes)}/{total}", flush=True)
    return codes


def main(total: int = 100_000, hot: int = 5000) -> None:
    bench_dir = Path(__file__).parent
    codes_path = bench_dir / "codes.txt"
    hot_path = bench_dir / "hot_codes.txt"

    try:
        codes = _insert_codes(codes_path, total)
    except Exception as exc:  # noqa: BLE001 给出可操作的提示
        msg = str(exc).lower()
        if "no such table" in msg or "does not exist" in msg:
            raise SystemExit(
                "数据库里没有 links 表，请先执行: uv run alembic upgrade head"
            ) from exc
        raise

    hot = min(hot, len(codes))
    hot_codes = secrets.SystemRandom().sample(codes, hot) if hot > 0 else []
    hot_path.write_text("\n".join(hot_codes) + "\n", encoding="utf-8")

    print(f"codes      -> {codes_path} ({len(codes)})")
    print(f"hot codes  -> {hot_path} ({len(hot_codes)})")


if __name__ == "__main__":
    total_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    hot_arg = int(sys.argv[2]) if len(sys.argv) > 2 else max(1, total_arg // 20)
    main(total_arg, hot_arg)
