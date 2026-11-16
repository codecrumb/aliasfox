import threading

from aliasfox.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_hit_allows_up_to_max_then_blocks():
    rl = RateLimiter(3, 60, clock=Clock())
    assert [rl.hit("a") for _ in range(5)] == [True, True, True, False, False]


def test_keys_are_independent():
    rl = RateLimiter(1, 60, clock=Clock())
    assert rl.hit("a") is True
    assert rl.hit("b") is True
    assert rl.hit("a") is False


def test_limiter_window_expires():
    clock = Clock()
    rl = RateLimiter(2, 60, clock=clock)
    assert rl.hit("a") and rl.hit("a")
    assert rl.hit("a") is False
    clock.t += 60.01
    assert rl.hit("a") is True


def test_blocked_does_not_record_and_retry_after():
    clock = Clock()
    rl = RateLimiter(1, 60, clock=clock)
    assert rl.blocked("a") is False
    assert rl.blocked("a") is False  # checking never records
    rl.hit("a")
    assert rl.blocked("a") is True
    clock.t += 10
    assert 49 <= rl.retry_after("a") <= 51
    clock.t += 51
    assert rl.blocked("a") is False
    assert rl.retry_after("a") == 0


def test_reset_clears_key():
    rl = RateLimiter(1, 60, clock=Clock())
    rl.hit("a")
    rl.reset("a")
    assert rl.hit("a") is True
    rl.reset("missing")  # no error


def test_empty_keys_are_dropped():
    clock = Clock()
    rl = RateLimiter(1, 10, clock=clock)
    for i in range(2000):
        rl.hit(f"k{i}")
    clock.t += 11
    rl.hit("fresh")
    assert len(rl._events) <= 2


def test_thread_safe_never_exceeds_max():
    rl = RateLimiter(50, 60)
    results = []

    def work():
        for _ in range(100):
            results.append(rl.hit("a"))

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 50
