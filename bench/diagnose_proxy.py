"""压测前的环境体检：确认本机 loopback 没被代理/虚拟网卡劫持。

为什么要有这个脚本：
压测里最贵的错误不是代码慢，是数据假。我机器上装过加速器，它会把系统代理和 TUN
都打开，连 `127.0.0.1:8000` 都可能被转发；表现是 P50 凭空多几毫秒、QPS 被代理
进程卡住，而代码怎么看都对。这种事靠感觉查不出来，得量。

做法：
1. 本机起一个只返回 `{"ok":true}` 的 echo 服务（不碰 DB/Redis）；
2. 用两个连接池分别打 N 次：一个 `trust_env=False`（绕开代理），
   一个 `trust_env=True`（走 requests 默认逻辑，会读环境变量和系统代理）；
3. loopback 的 P50 应该是亚毫秒级，两者差超过 1ms 就说明本地请求被代理接走了；
4. 顺手把三层配置打出来：虚拟网卡 / WinHTTP / 环境变量。

另外单独量一次「短连接建立开销」：这个数高不代表被污染，只说明不复用连接很贵，
压测脚本必须用连接池（locust 的 requests.Session 默认就是）。

干净的标准：两种客户端的 P50 都 < 1ms 且相差 < 1ms，退出码 0。
不干净就退出码 1，先修环境再压测，别拿这批数据写报告。

用法：
    uv run python bench/diagnose_proxy.py            # 默认 1000 次
    uv run python bench/diagnose_proxy.py -n 2000
"""

from __future__ import annotations

import argparse
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

THRESHOLD_MS = 1.0
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


class _EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs) -> None:  # 静音访问日志
        return


def start_echo() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def bench_session(port: int, n: int, *, trust_env: bool) -> tuple[list[float], str]:
    """用连接池打 n 次，返回每次耗时（毫秒）。"""
    session = requests.Session()
    session.trust_env = trust_env
    url = f"http://127.0.0.1:{port}/healthz"
    samples: list[float] = []
    try:
        session.get(url, timeout=5)  # 预热，把首连接开销排除掉
        for _ in range(n):
            start = time.perf_counter()
            session.get(url, timeout=5)
            samples.append((time.perf_counter() - start) * 1000)
    except Exception as exc:  # noqa: BLE001 请求失败本身就是结论
        return samples, f"{type(exc).__name__}: {exc}"
    finally:
        session.close()
    return samples, ""


def bench_fresh_connection(port: int, n: int = 100) -> float:
    """只测 TCP 建连的耗时，不复用连接。用来解释「为什么必须开 keep-alive」。"""
    samples: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        samples.append((time.perf_counter() - start) * 1000)
        sock.close()
    return statistics.median(samples) if samples else float("nan")


def pct(samples: list[float], p: float) -> float:
    if not samples:
        return float("nan")
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return ordered[idx]


def run_cmd(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, errors="ignore")
        return (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<执行失败: {exc}>"


def check_layers() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"env": [], "winhttp": [], "tun": []}

    for name in PROXY_VARS:
        value = os.getenv(name)
        if value:
            out["env"].append(f"{name}={value}")
    if not out["env"]:
        out["env"].append("没有设置任何代理环境变量")

    if platform.system() == "Windows":
        listing = run_cmd(["netsh", "winhttp", "show", "proxy"]).splitlines()
        out["winhttp"].append(listing[-1].strip() if listing else "<无输出>")
        adapters = run_cmd(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-NetAdapter | Where-Object Status -eq 'Up' | "
                "Select-Object -ExpandProperty InterfaceDescription",
            ]
        )
        suspects = [
            line
            for line in adapters.splitlines()
            if any(k in line.lower() for k in ("tap", "tun", "wireguard", "clash", "meta", "vpn"))
        ]
        out["tun"].extend(suspects or ["没有发现 TAP/TUN/WireGuard 之类的虚拟网卡"])
    else:
        out["winhttp"].append("非 Windows，跳过 WinHTTP 检查")
        out["tun"].append("非 Windows，跳过网卡检查")

    try:
        infos = socket.getaddrinfo("localhost", 80, proto=socket.IPPROTO_TCP)
        addresses = sorted({info[4][0] for info in infos})
        out["tun"].append(f"localhost 解析为 {', '.join(addresses)}")
    except OSError as exc:
        out["tun"].append(f"localhost 解析失败: {exc}")

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="压测前检查本机 loopback 是否被代理污染")
    ap.add_argument("-n", "--requests", type=int, default=1000, help="每个客户端打多少次")
    args = ap.parse_args()

    server, port = start_echo()
    print(f'echo 服务已起在 127.0.0.1:{port}（只回 {{"ok":true}}，不碰 DB/Redis）')

    try:
        clean, clean_err = bench_session(port, args.requests, trust_env=False)
        env_client, env_err = bench_session(port, args.requests, trust_env=True)
        fresh_ms = bench_fresh_connection(port)
    finally:
        server.shutdown()
        server.server_close()

    print()
    print(f"{'客户端':<16}{'次数':>6}{'P50(ms)':>10}{'P95(ms)':>10}{'P99(ms)':>10}")
    print("-" * 54)
    for label, samples, err in (
        ("连接池(绕代理)", clean, clean_err),
        ("连接池(默认配置)", env_client, env_err),
    ):
        if not samples:
            print(f"{label:<16}{0:>6}{'—':>10}{'—':>10}{'—':>10}   {err}")
            continue
        print(
            f"{label:<16}{len(samples):>6}{pct(samples, 50):>10.3f}"
            f"{pct(samples, 95):>10.3f}{pct(samples, 99):>10.3f}"
        )
    print(
        f"\n短连接（每次新建 TCP）建连 P50：{fresh_ms:.3f}ms"
        "  ← 这个数高只说明不复用连接贵，不代表被代理"
    )

    print()
    layers = check_layers()
    print("[L1 虚拟网卡 / TUN]")
    for line in layers["tun"]:
        print(f"  {line}")
    print("[L2 系统代理 WinHTTP]")
    for line in layers["winhttp"]:
        print(f"  {line}")
    print("[L3 环境变量]")
    for line in layers["env"]:
        print(f"  {line}")

    print()
    clean_p50 = pct(clean, 50) if clean else float("inf")
    env_p50 = pct(env_client, 50) if env_client else float("inf")
    gap = abs(env_p50 - clean_p50)
    env_proxy = [name for name in PROXY_VARS if os.getenv(name)]

    problems: list[str] = []
    if clean_err:
        problems.append(f"直连都失败了（{clean_err}），先看服务是不是被别的进程占了")
    if clean_p50 > THRESHOLD_MS:
        problems.append(f"绕代理时 P50 {clean_p50:.3f}ms 超过 {THRESHOLD_MS}ms，loopback 有中间层")
    if env_err:
        problems.append(f"默认配置下请求失败（{env_err}），说明代理把本地请求接走了")
    elif env_client and gap > THRESHOLD_MS:
        problems.append(f"两种客户端 P50 相差 {gap:.3f}ms，本地请求受到了代理影响")

    if problems:
        print("结论：不干净，先修环境再压测。")
        for item in problems:
            print(f"  - {item}")
        return 1

    print(
        f"结论：干净。绕代理 P50 {clean_p50:.3f}ms，默认配置 {env_p50:.3f}ms，"
        f"阈值 {THRESHOLD_MS}ms。"
    )
    if env_proxy:
        print(f"  提醒：环境里仍有 {', '.join(env_proxy)}，压测时建议清掉，避免子进程读到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
