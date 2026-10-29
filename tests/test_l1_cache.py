import time

from app.services.l1_cache import TTLCache


def test_set_then_get():
    c = TTLCache(maxsize=10, ttl=30)
    c.set("a", {"url": "https://x"})
    assert c.get("a") == {"url": "https://x"}
    assert c.hits == 1 and c.misses == 0


def test_expired_entry_returns_none():
    c = TTLCache(maxsize=10, ttl=0.02)
    c.set("a", 1)
    time.sleep(0.05)
    assert c.get("a") is None
    assert c.misses == 1


def test_lru_eviction():
    c = TTLCache(maxsize=2, ttl=30)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")  # a 变成最近使用
    c.set("c", 3)  # 淘汰最久未用的 b
    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3


def test_delete_and_clear():
    c = TTLCache(maxsize=10, ttl=30)
    c.set("a", 1)
    c.delete("a")
    assert c.get("a") is None
    c.set("b", 2)
    c.clear()
    assert c.size == 0
