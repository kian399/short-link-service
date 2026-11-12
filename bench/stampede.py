"""singleflight 的定向验证：造「缓存刚失效 + 同一热点码」的回源尖峰。

为什么不在五组里验证：
80/20 混合流量下，每个码的未命中窗口只有几毫秒，200 个并发撞上同一个冷码的
概率极低——实测 D 组 `singleflight_total{result="leader_ok"} 25350`、
`result="shared"` 一条都没有。也就是说五组里的 singleflight 从来没合并过任何请求，
D 组和 C 组的差异只是噪声。

真实场景里单飞要挡的是「热点码 TTL 到期瞬间，几百个请求同时进来」。
所以这里把变量压到最小：只打一个码、L1 TTL 压到 1 秒（每秒必然失效一次）、
关掉 L2（失效就得回源 PG），然后开/关 singleflight 各跑 60 秒比回源次数。

用法：
    .venv/Scripts/python.exe bench/stampede.py --port 8001
"""

from __future__ import annotations

import argparse
import subprocess
import time
import urllib.request

from run_all_groups import OUT, PY, ROOT, child_env, flush_redis, http_ok, log

HOT = ROOT / "bench" / "hot_codes.txt"


def wait_ready(proc: subprocess.Popen, host: str) -> bool:
    for _ in range(60):
        if http_ok(f"{host}/healthz"):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(1)
    return False


def run_case(
    name: str, host: str, code: str, singleflight: bool, l1_ttl: str, duration: str
) -> dict:
    log(f"\n---- 场景 {name}（singleflight={'on' if singleflight else 'off'}）----")
    flush_redis()

    env = child_env(
        CACHE_L1_ENABLED="true",
        CACHE_L2_ENABLED="false",  # 关掉 L2，L1 一失效就必须回源 PG
        SINGLEFLIGHT_ENABLED="true" if singleflight else "false",
        STATS_RECORD_ENABLED="false",
        STATS_FLUSH_ENABLED="false",
        L1_CACHE_TTL=l1_ttl,
    )
    app_log = (OUT / f"{name}_uvicorn.log").open("w", encoding="utf-8")
    port = host.rsplit(":", 1)[1]
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", port, "--workers", "1"],
        cwd=ROOT,
        env=env,
        stdout=app_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        if not wait_ready(proc, host):
            raise SystemExit(f"服务起不来，看 bench/out/{name}_uvicorn.log")

        subprocess.run(
            [
                PY,
                "-m",
                "locust",
                "-f",
                "bench/loadtest.py",
                "--headless",
                "-u",
                "200",
                "-r",
                "200",
                "-t",
                duration,
                "--host",
                host,
                "--csv",
                str(OUT / name),
                "--only-summary",
            ],
            cwd=ROOT,
            env=child_env(BENCH_FIXED_CODE=code),
            capture_output=True,
            text=True,
        )

        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"{host}/metrics", timeout=5
        ) as resp:
            text = resp.read().decode()
        (OUT / f"{name}_metrics.txt").write_text(text, encoding="utf-8")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        app_log.close()

    def num(pattern: str) -> float:
        for line in text.splitlines():
            if line.startswith(pattern):
                return float(line.rsplit(" ", 1)[1])
        return 0.0

    seconds = int(duration.rstrip("sm")) * (60 if duration.endswith("m") else 1)
    leaders = num('singleflight_total{result="leader_ok"}')
    shared = num('singleflight_total{result="shared"}')
    db_hits = num('cache_lookups_total{layer="l1",result="miss"}')
    log(
        f"回源(leader)={leaders:.0f} 合并(shared)={shared:.0f} L1未命中={db_hits:.0f} "
        f"→ {(leaders or db_hits) / seconds:.0f} 次 DB 查询/秒"
    )
    return {
        "name": name,
        "leaders": leaders,
        "shared": shared,
        "l1_miss": db_hits,
        "seconds": seconds,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--duration", default="60s")
    ap.add_argument("--ttl", default="1")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    codes = HOT.read_text(encoding="utf-8").split()
    if not codes:
        raise SystemExit("bench/hot_codes.txt 为空，先跑 bench/seed.py")
    code = codes[0]
    host = f"http://127.0.0.1:{args.port}"
    log(f"钉死短码 /{code}，L1 TTL={args.ttl}s，L2 关闭，200 并发")

    a = run_case("S_nosf", host, code, False, args.ttl, args.duration)
    time.sleep(5)
    b = run_case("S_sf", host, code, True, args.ttl, args.duration)

    print("\n| 场景 | 回源 DB 次数 | singleflight 合并次数 | 折合 DB QPS |")
    print("|---|---|---|---|")
    for r in (a, b):
        label = "关单飞" if r["name"] == "S_nosf" else "开单飞"
        db_qps = (r["leaders"] or r["l1_miss"]) / r["seconds"]
        print(
            f"| {label} | {r['leaders'] or r['l1_miss']:.0f} | {r['shared']:.0f} | {db_qps:.0f} |"
        )
    # 关单飞时没有 leader 计数，回源次数就等于 L1 未命中数；
    # 开单飞时每个失效窗口只有一个 leader 去查库，shared 是被合并掉的那部分。
    db_queries = b["leaders"] if b["leaders"] else b["l1_miss"]
    base = a["l1_miss"]
    if base and db_queries:
        print(f"\n回源压降：{1 - db_queries / base:.1%}（{base:.0f} → {db_queries:.0f} 次）")


if __name__ == "__main__":
    main()
