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
    g, c = make(per_user=2, cooldown_seconds=30)
    for _ in range(3):
        g.record_error(1)
    assert g.check(1) == 30 and g.check(1) == 30  # denied twice during cooldown
    c.t += 31  # cooldown over, the 60 s hit window has not elapsed
    assert g.check(1) is None and g.check(1) is None  # both hits still available
    assert g.check(1) >= 1
    for _ in range(3):
        g.record_error(1)
    g.reset_user(1)
    assert g.check(1) is None  # cooldown and hits gone


def test_global_denial_returns_the_users_hit():
    g, c = make(per_user=1, global_per_minute=2)
    assert g.check(1) is None  # t0: user 1 + global slot
    c.t += 40
    assert g.check(2) is None  # fills global
    c.t += 1
    assert g.check(3) >= 1  # denied by global at t=41
    c.t += 20  # t=61: user 1's global slot expired, user 3's t=41 hit would still be in window
    assert g.check(3) is None


def test_per_user_denial_leaves_global_capacity_intact():
    g, c = make(per_user=1, global_per_minute=2)
    assert g.check(1) is None
    assert all(g.check(1) >= 1 for _ in range(5))  # denied per-user, no global hits
    assert g.check(2) is None  # the second global slot is still free


def test_sweep_drops_expired_cooldowns():
    g, c = make()
    for _ in range(3):
        g.record_error(1)
    g.record_error(2)
    c.t += 301
    g.sweep()
    assert g._cool == {}
