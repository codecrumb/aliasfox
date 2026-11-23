from aliasfox.createguard import CreateGuard


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    c = Clock()
    return CreateGuard(clock=c, **kw), c


def test_per_user_11th_hit_denied_with_retry_after():
    g, c = make()
    assert all(g.check(1) is None for _ in range(10))
    assert g.check(1) >= 1


def test_global_ceiling_denies_other_user():
    g, c = make(per_user=100, global_per_minute=3)
    assert all(g.check(u) is None for u in (1, 2, 3))
    assert g.check(4) >= 1
    c.t += 61
    assert g.check(4) is None


def test_three_errors_start_300s_cooldown_then_ok_at_301s():
    g, c = make()
    for _ in range(3):
        g.record_error(1)
    assert g.check(1) == 300
    c.t += 100
    assert g.check(1) == 200
    c.t += 201
    assert g.check(1) is None


def test_success_resets_error_count():
    g, c = make()
    g.record_error(1)
    g.record_error(1)
    g.record_success(1)
    g.record_error(1)
    g.record_error(1)
    assert g.check(1) is None


def test_errors_are_per_user():
    g, c = make()
    for _ in range(3):
        g.record_error(1)
    assert g.check(2) is None


def test_denied_check_does_not_extend_cooldown():
    g, c = make()
    for _ in range(3):
        g.record_error(1)
    c.t += 200
    assert g.check(1) == 100
    g.record_error(1)  # stray in-flight error during cooldown
    assert g.check(1) == 100


def test_cooldown_denial_consumes_no_hit_and_reset_user_clears():
    g, c = make(per_user=1)
    for _ in range(3):
        g.record_error(1)
    g.check(1)
    g.reset_user(1)
    assert g.check(1) is None  # hit budget untouched and cooldown gone
    assert g.check(1) >= 1


def test_sweep_drops_expired_cooldowns():
    g, c = make()
    for _ in range(3):
        g.record_error(1)
    g.record_error(2)
    c.t += 301
    g.sweep()
    assert g._cool == {}
