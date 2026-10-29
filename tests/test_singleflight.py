import threading
import time

from app.services.singleflight import SingleFlight


def test_no_overlapping_executions():
    """核心不变量：同一 key 任何时刻最多一个回源在执行。"""
    sf = SingleFlight()
    state = {"in_flight": 0, "overlap": 0}
    lock = threading.Lock()
    results = []

    def work():
        with lock:
            state["in_flight"] += 1
            if state["in_flight"] > 1:
                state["overlap"] += 1
        time.sleep(0.05)
        with lock:
            state["in_flight"] -= 1
        return "value"

    n = 20
    barrier = threading.Barrier(n)

    def run():
        barrier.wait()
        results.append(sf.do("key", work))

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["overlap"] == 0, "出现了并发回源，singleflight 失效"
    assert results == ["value"] * n


def test_waiters_share_one_slow_call():
    """回源够慢时，20 个并发只应执行一次。"""
    sf = SingleFlight()
    calls = []

    def work():
        calls.append(1)
        time.sleep(0.3)
        return "value"

    n = 20
    barrier = threading.Barrier(n)
    results = []

    def run():
        barrier.wait()
        results.append(sf.do("key", work))

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert results == ["value"] * n
    assert sf.shared == n - 1


def test_error_propagates_to_all_waiters():
    sf = SingleFlight()
    errors = []

    def boom():
        raise ValueError("db down")

    n = 10
    barrier = threading.Barrier(n)

    def run():
        barrier.wait()
        try:
            sf.do("key", boom)
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == ["db down"] * n


def test_different_keys_do_not_share():
    sf = SingleFlight()
    results = {}

    def run(key):
        results[key] = sf.do(key, lambda: key.upper())

    threads = [threading.Thread(target=run, args=(k,)) for k in ("a", "b", "c")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == {"a": "A", "b": "B", "c": "C"}


def test_leader_can_retry_after_success():
    sf = SingleFlight()
    assert sf.do("k", lambda: 1) == 1
    assert sf.do("k", lambda: 2) == 2
