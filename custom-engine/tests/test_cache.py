"""Offline tests for search.cache (fake clock, no sleeping)."""

import threading
import time

import pytest

from search import cache
from search.cache import TTLLRUCache, make_key


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


def test_miss_returns_none():
    assert TTLLRUCache().get("nope") is None


def test_set_get():
    c = TTLLRUCache()
    c.set("a", {"x": 1})
    assert c.get("a") == {"x": 1}
    assert "a" in c
    assert len(c) == 1


def test_ttl_expiry():
    clk = FakeClock()
    c = TTLLRUCache(ttl=600.0, clock=clk)
    c.set("a", 1)
    clk.advance(599)
    assert c.get("a") == 1
    assert "a" in c
    clk.advance(2)  # 601 s since set (hit does not refresh TTL)
    assert "a" not in c
    assert c.get("a") is None


def test_expired_removed_from_len():
    clk = FakeClock()
    c = TTLLRUCache(ttl=10, clock=clk)
    c.set("a", 1)
    c.set("b", 2)
    assert len(c) == 2
    clk.advance(11)
    assert len(c) == 0
    assert c.get("a") is None


def test_set_purges_expired():
    clk = FakeClock()
    c = TTLLRUCache(maxsize=3, ttl=10, clock=clk)
    c.set("a", 1)
    c.set("b", 2)
    clk.advance(11)
    c.set("c", 3)
    assert len(c) == 1
    assert c.get("c") == 3


def test_set_existing_resets_timestamp():
    clk = FakeClock()
    c = TTLLRUCache(ttl=10, clock=clk)
    c.set("a", 1)
    clk.advance(8)
    c.set("a", 2)
    clk.advance(8)
    assert c.get("a") == 2


def test_lru_eviction_order():
    c = TTLLRUCache(maxsize=3)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") == 1  # a becomes most recent; b is now oldest
    c.set("d", 4)
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3
    assert c.get("d") == 4
    assert len(c) == 3


def test_set_existing_moves_to_end():
    c = TTLLRUCache(maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("a", 10)  # a most recent
    c.set("c", 3)  # evicts b
    assert c.get("b") is None
    assert c.get("a") == 10


def test_default_size_100_evicts_oldest():
    c = TTLLRUCache()
    for i in range(100):
        c.set(f"k{i}", i)
    assert len(c) == 100
    c.set("k100", 100)
    assert len(c) == 100
    assert c.get("k0") is None
    assert c.get("k1") == 1
    assert c.get("k100") == 100


def test_clear():
    c = TTLLRUCache()
    c.set("a", 1)
    c.clear()
    assert len(c) == 0
    assert c.get("a") is None


def test_make_key_properties():
    k = make_key("Hello   World", True, "en")
    assert k == make_key("hello world", True, "EN")
    assert k == make_key("  HELLO\tworld ", True, "en")
    assert k != make_key("hello world", False, "en")
    assert k != make_key("hello world", True, "de")
    assert len(k) == 64
    int(k, 16)
    assert "hello" not in k and "world" not in k


def test_thread_safety_smoke():
    c = TTLLRUCache(maxsize=50)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(500):
                key = f"k{(n * 7 + i) % 120}"
                c.set(key, i)
                c.get(key)
                _ = key in c
                len(c)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(c) <= 50


def test_module_proxies():
    cache.clear()
    cache.set("k", "v")
    assert cache.get("k") == "v"
    assert "k" in cache.default_cache
    cache.clear()
    assert cache.get("k") is None
    assert cache.default_cache._maxsize == 100
    assert cache.default_cache._ttl == 600.0


@pytest.mark.parametrize("kwargs", [{"maxsize": 0}, {"maxsize": -1}, {"ttl": 0}, {"ttl": -5.0}])
def test_bad_args(kwargs):
    with pytest.raises(ValueError):
        TTLLRUCache(**kwargs)


def test_get_speed_1000_entries():
    c = TTLLRUCache(maxsize=1000)
    for i in range(1000):
        c.set(f"k{i}", i)
    start = time.perf_counter()
    for i in range(1000):
        c.get(f"k{i}")
    assert time.perf_counter() - start < 0.1
