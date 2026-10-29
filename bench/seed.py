"""造压测数据：直接往库里灌 N 条短链，并把短码写到 bench/codes.txt。

用法：
    uv run python bench/seed.py 100000
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from app.db import SessionLocal
from app.models.link import Link
from app.services.shortener import BASE62_ALPHABET

CHUNK = 5000


def main(total: int = 100_000) -> None:
    out = Path(__file__).with_name("codes.txt")
    written = 0
    with SessionLocal() as db, out.open("w", encoding="utf-8") as fh:
        while written < total:
            batch = []
            for _ in range(min(CHUNK, total - written)):
                code = "".join(secrets.choice(BASE62_ALPHABET) for _ in range(7))
                batch.append(Link(code=code, original_url=f"https://example.com/{code}"))
                fh.write(code + "\n")
            db.add_all(batch)
            db.commit()
            written += len(batch)
            print(f"seeded {written}/{total}", flush=True)
    print(f"codes written to {out}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
