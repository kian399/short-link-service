"""压测时在另一个进程里采样：主机 CPU、应用进程 CPU、PG 连接数。

为什么要单独一个进程：在压测机上跟 locust 抢同一个解释器，采样本身就会污染数据。
这个脚本只做三件事，5 秒一次，开销可以忽略：
- psutil 读主机 CPU 和 uvicorn 进程 CPU（多 worker 时把所有子进程加起来）；
- 直连 PG 查 pg_stat_activity 的活跃连接数（端口是 compose 映射出来的）；
- docker stats 顺带记一下中间件容器的 CPU。

输出 CSV，配合 bench/out 里的 locust 结果一起看，就能回答
「到底是应用 CPU 满了，还是数据库连接不够，还是数据库自己在忙」。

用法：
    uv run python bench/sample_system.py --duration 900 --out bench/out/worker_sampler.csv
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# 脚本以 bench/ 为 sys.path[0]，把项目根目录加进来才能 import app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def uvicorn_procs() -> list[psutil.Process]:
    """找出 uvicorn 的进程树。

    单 worker 时父进程的 cmdline 里带 `uvicorn app.main:app`；
    多 worker 时子进程是 multiprocessing spawn 出来的，cmdline 变成
    `python -c "from multiprocessing..."`，所以得靠这个特征一起捞。
    locust 也是 python 进程，按 cmdline 排掉。
    """
    out: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.Error, TypeError):
            continue
        if "locust" in cmdline or not cmdline:
            continue
        if "app.main:app" in cmdline or "multiprocessing" in cmdline:
            out.append(proc)
    return out


def docker_cpu() -> str:
    try:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}:{{.CPUPerc}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [
        line.strip() for line in proc.stdout.splitlines() if line.startswith("short-link-service-")
    ]
    return ";".join(line.replace("short-link-service-", "").replace("-1:", ":") for line in lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=900, help="采样多少秒")
    ap.add_argument("--interval", type=float, default=5.0, help="采样间隔（秒）")
    ap.add_argument("--out", default="bench/out/worker_sampler.csv")
    args = ap.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.DATABASE_URL, future=True)
    queries = {
        "active": "select count(*) from pg_stat_activity "
        "where datname = 'shortlink' and state = 'active'",
        "total": "select count(*) from pg_stat_activity where datname = 'shortlink'",
    }
    psutil.cpu_percent(interval=None)  # 第一次调用总是 0，先空转一次

    deadline = time.time() + args.duration
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["ts", "host_cpu", "app_cpu", "app_procs", "pg_active", "pg_total", "docker_cpu"]
        )
        while time.time() < deadline:
            procs = uvicorn_procs()
            app_cpu = 0.0
            for proc in procs:
                try:
                    app_cpu += proc.cpu_percent(interval=None)
                except psutil.Error:
                    continue
            try:
                with engine.connect() as conn:
                    active = conn.execute(text(queries["active"])).scalar_one()
                    total = conn.execute(text(queries["total"])).scalar_one()
            except Exception:  # noqa: BLE001 PG 没起来就记空
                active, total = "", ""
            writer.writerow(
                [
                    datetime.now(UTC).strftime("%H:%M:%S"),
                    f"{psutil.cpu_percent(interval=None):.1f}",
                    f"{app_cpu:.1f}",
                    len(procs),
                    active,
                    total,
                    docker_cpu(),
                ]
            )
            fh.flush()
            time.sleep(args.interval)

    engine.dispose()
    print(f"采样结束，写入 {out_path}")


if __name__ == "__main__":
    main()
