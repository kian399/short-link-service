"""五组压测一键执行：A 纯DB → B +Redis → C +L1 → D +singleflight → E +埋点。

为什么要有这个脚本：
手工开五个终端、每组改四个开关、忘了 flushdb、忘了换 CSV 前缀，
五组之间就不可比，等于白跑。这里把每一步固定下来。

为什么用 Python 而不是 bash：
Git Bash 里 `kill $!` 会把整个作业组带走，脚本自己被 SIGTERM，
前两组跑完就断，排查一次浪费十分钟。Windows 上 subprocess 走
CREATE_NEW_PROCESS_GROUP + terminate 才是可靠的。

用法（仓库根目录）：
    .venv/Scripts/python.exe bench/run_all_groups.py            # 五组，每组 3 分钟
    .venv/Scripts/python.exe bench/run_all_groups.py A C        # 只跑指定组
    .venv/Scripts/python.exe bench/run_all_groups.py --duration 1m --users 50

前置：docker compose up -d postgres redis；已执行 bench/seed.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench" / "out"
PORT = int(os.getenv("BENCH_PORT", "8000"))
HOST = f"http://127.0.0.1:{PORT}"
PY = sys.executable

# 每组的开关组合；A–D 关埋点，E 全开，这样 E−D 的差值就是「一次 Redis INCR」的代价
GROUP_ENV: dict[str, dict[str, str]] = {
    "A": {
        "CACHE_L1_ENABLED": "false",
        "CACHE_L2_ENABLED": "false",
        "SINGLEFLIGHT_ENABLED": "false",
        "STATS_RECORD_ENABLED": "false",
        "STATS_FLUSH_ENABLED": "false",
    },
    "B": {
        "CACHE_L1_ENABLED": "false",
        "CACHE_L2_ENABLED": "true",
        "SINGLEFLIGHT_ENABLED": "false",
        "STATS_RECORD_ENABLED": "false",
        "STATS_FLUSH_ENABLED": "false",
    },
    "C": {
        "CACHE_L1_ENABLED": "true",
        "CACHE_L2_ENABLED": "true",
        "SINGLEFLIGHT_ENABLED": "false",
        "STATS_RECORD_ENABLED": "false",
        "STATS_FLUSH_ENABLED": "false",
    },
    "D": {
        "CACHE_L1_ENABLED": "true",
        "CACHE_L2_ENABLED": "true",
        "SINGLEFLIGHT_ENABLED": "true",
        "STATS_RECORD_ENABLED": "false",
        "STATS_FLUSH_ENABLED": "false",
    },
    "E": {
        "CACHE_L1_ENABLED": "true",
        "CACHE_L2_ENABLED": "true",
        "SINGLEFLIGHT_ENABLED": "true",
        "STATS_RECORD_ENABLED": "true",
        "STATS_FLUSH_ENABLED": "true",
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


# 本机请求必须绕开系统代理。
# 踩过的坑：环境里有 HTTP_PROXY=127.0.0.1:56475（本地加速器/抓包工具留的），
# 而 NO_PROXY 是空的，于是 locust、prewarm、urllib 全部把 127.0.0.1:8000 交给代理转发：
# 吞吐被代理吃掉一半，代理抖一下就批量 status 0，/metrics 直接 502。
# 这类噪声不会报错，只会让你对着一堆「看起来很真」的假数据写报告。
LOCAL_HOSTS = "127.0.0.1,localhost,::1"
_BYPASS = {"NO_PROXY": LOCAL_HOSTS, "no_proxy": LOCAL_HOSTS}


def child_env(**overrides: str) -> dict[str, str]:
    return {**os.environ, **_BYPASS, **overrides}


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with _OPENER.open(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def port_free() -> bool:
    """8000 上不该有任何服务；有的话说明上一组没杀干净，必须停下来。"""
    for _ in range(30):
        if not http_ok(f"{HOST}/healthz"):
            return True
        time.sleep(1)
    return False


def run(cmd: list[str], env: dict[str, str] | None = None) -> int:
    proc = subprocess.run(
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    if proc.returncode != 0:
        log(proc.stdout[-2000:])
        log(proc.stderr[-2000:])
    return proc.returncode


def flush_redis() -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "flushdb"],
        cwd=ROOT,
        capture_output=True,
    )


def run_group(
    group: str,
    duration: str,
    users: int,
    spawn: int,
    cooldown: int,
    workers: int = 1,
    tag: str = "",
) -> None:
    log(f"\n==================== 组 {group} ====================")
    if not port_free():
        raise SystemExit("!! 端口 8000 仍被占用，先手动 kill 掉旧 uvicorn")

    # ① 每组从空缓存开始，否则后一组白占前一组的便宜
    flush_redis()

    # ② 起服务，开关由环境变量注入（优先级高于 .env）
    # WEB_CONCURRENCY 让 app/db.py 按 worker 数均分连接池额度
    env = child_env(**GROUP_ENV[group], WEB_CONCURRENCY=str(workers))
    log("开关: " + " ".join(f"{k}={v}" for k, v in GROUP_ENV[group].items()))
    app_log = (OUT / f"{group}{tag}_uvicorn.log").open("w", encoding="utf-8")
    app = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--workers", str(workers)],
        cwd=ROOT,
        env=env,
        stdout=app_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    try:
        ready = False
        for _ in range(60):
            if http_ok(f"{HOST}/healthz"):
                ready = True
                break
            if app.poll() is not None:
                break
            time.sleep(1)
        if not ready:
            raise SystemExit(f"!! 组 {group} 服务起不来，看 bench/out/{group}_uvicorn.log")
        log(f"服务就绪 pid={app.pid}")

        # ③ 预热热点码；A 组也打一遍，保证五组起点一致
        if run([PY, "bench/prewarm.py", HOST]) != 0:
            raise SystemExit("!! 预热失败")

        # ④ 压测
        rc = run(
            [
                PY,
                "-m",
                "locust",
                "-f",
                "bench/loadtest.py",
                "--headless",
                "-u",
                str(users),
                "-r",
                str(spawn),
                "-t",
                duration,
                "--host",
                HOST,
                "--csv",
                str(OUT / f"{group}{tag}"),
                "--only-summary",
            ]
        )
        if rc != 0:
            # 不当致命错误：locust 偶尔在收尾瞬间给在途请求记 status=0，
            # 那种「错误」不是服务端挂了，直接丢弃整组数据反而要重跑 5 分钟。
            log(f"!! 组 {group} locust 返回 {rc}，请看 bench/out/{group}_stats.csv 判断")

        # ⑤ 抓指标快照（命中率/连接池），必须在停服务之前
        try:
            with _OPENER.open(f"{HOST}/metrics", timeout=5) as resp:
                (OUT / f"{group}{tag}_metrics.txt").write_text(
                    resp.read().decode(), encoding="utf-8"
                )
        except Exception as exc:  # noqa: BLE001
            log(f"指标抓取失败（不影响压测结果）: {exc}")
    finally:
        app.terminate()
        try:
            app.wait(timeout=15)
        except subprocess.TimeoutExpired:
            app.kill()
        app_log.close()

    log(f"组 {group} 结束，冷却 {cooldown} 秒让 PG 连接池回收")
    time.sleep(cooldown)


def main() -> None:
    global PORT, HOST
    ap = argparse.ArgumentParser()
    ap.add_argument("groups", nargs="*", default=None, help="要跑的组，默认 A B C D E")
    ap.add_argument("--duration", default="3m")
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--spawn", type=int, default=20)
    ap.add_argument("--cooldown", type=int, default=30)
    ap.add_argument("--port", type=int, default=PORT, help="本机端口，8000 被占时换一个")
    ap.add_argument("--workers", type=int, default=1, help="uvicorn worker 数，默认 1")
    ap.add_argument("--tag", default="", help="输出文件名后缀，避免覆盖已有结果，如 --tag _w4")
    args = ap.parse_args()

    if args.port != PORT:
        PORT = args.port
        HOST = f"http://127.0.0.1:{PORT}"
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [g.upper() for g in (args.groups or list(GROUP_ENV))]

    for g in targets:
        if g not in GROUP_ENV:
            raise SystemExit(f"未知组 {g}")
        run_group(g, args.duration, args.users, args.spawn, args.cooldown, args.workers, args.tag)

    log("\n==================== 汇总 ====================")
    subprocess.run([PY, "bench/summarize.py"], cwd=ROOT)


if __name__ == "__main__":
    main()
