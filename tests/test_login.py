import os
import threading

import pytest

from aliasfox.apple.login import (
    AppleLoginFailed,
    FlowExpired,
    InvalidCode,
    LoginFlow,
    TooManyFlows,
)

PASSWORD = "hunter2-SECRET-pw"
APPLE_ID = "someone@example.com"


class FakeJar(list):
    pass


class FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class FakeSession:
    def __init__(self):
        self.cookies = FakeJar([FakeCookie("X-APPLE-WEBAUTH-USER", "cookieval"),
                                FakeCookie("aasp", "aaspval")])
        self.data = {"trust_token": "TRUST", "session_id": "sid"}


class FakeService:
    instances: list = []
    requires_2fa = False
    requires_2sa = False
    good_code = "123456"
    auth_error: Exception | None = None
    request_error: Exception | None = None
    kind = "trusted_device"

    def __init__(self, apple_id, password, cookie_directory, china_mainland):
        if self.auth_error:
            raise self.auth_error
        assert os.path.isdir(cookie_directory)
        self.cookie_directory = cookie_directory
        self.china = china_mainland
        self.password_seen = password
        self.session = FakeSession()
        self.data = {"dsInfo": {"dsid": "999"}}
        self.webservices = {"premiummailsettings": {"url": "https://p1-maildomainws.icloud.com:443"}}
        self.two_factor_delivery_method = self.kind
        self.requested = False
        self.attempts = 0
        self.instances.append(self)

    def request_2fa_code(self):
        self.requested = True
        if self.request_error:
            raise self.request_error

    def validate_2fa_code(self, code):
        self.attempts += 1
        return code == self.good_code


def make_factory(**attrs):
    cls = type("F", (FakeService,), {"instances": [], **attrs})

    def factory(apple_id, password, *, cookie_directory, china_mainland=False):
        return cls(apple_id, password, cookie_directory, china_mainland)

    factory.cls = cls
    return factory


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def flow(factory=None, **kw):
    factory = factory or make_factory(requires_2fa=True)
    return LoginFlow(service_factory=factory, **kw), factory


def test_no_2fa_returns_ok_with_state():
    f, fac = flow(make_factory(requires_2fa=False))
    res = f.start(1, APPLE_ID, PASSWORD, china=True)
    assert res.status == "ok" and res.flow_id is None
    st = res.state
    assert st.cookies == {"X-APPLE-WEBAUTH-USER": "cookieval", "aasp": "aaspval"}
    assert st.webservices_url == "https://p1-maildomainws.icloud.com:443"
    assert st.dsid == "999"
    assert st.extra == {"trust_token": "TRUST"}
    inst = fac.cls.instances[0]
    assert inst.china is True
    assert not os.path.exists(inst.cookie_directory)


def test_needs_2fa_then_verify():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    assert res.status == "needs_2fa" and res.state is None
    assert len(res.flow_id) >= 43
    inst = fac.cls.instances[0]
    assert inst.requested
    assert os.path.isdir(inst.cookie_directory)
    assert oct(os.stat(inst.cookie_directory).st_mode & 0o777) == "0o700"
    st = f.verify(1, res.flow_id, "123456")
    assert st.webservices_url.startswith("https://p1-maildomainws")
    assert not os.path.exists(inst.cookie_directory)
    with pytest.raises(FlowExpired):
        f.verify(1, res.flow_id, "123456")


def test_temp_dir_removed_on_auth_failure(monkeypatch):
    created = []
    import tempfile

    real = tempfile.mkdtemp

    def spy(*a, **k):
        d = real(*a, **k)
        created.append(d)
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    f, _ = flow(make_factory(auth_error=RuntimeError(f"bad {PASSWORD} {APPLE_ID}")))
    with pytest.raises(AppleLoginFailed) as ei:
        f.start(1, APPLE_ID, PASSWORD)
    assert created and not os.path.exists(created[0])
    assert PASSWORD not in str(ei.value) and APPLE_ID not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_temp_dir_removed_on_cancel():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    d = fac.cls.instances[0].cookie_directory
    f.cancel(res.flow_id)
    assert not os.path.exists(d)
    with pytest.raises(FlowExpired):
        f.verify(1, res.flow_id, "123456")
    f.cancel("nope")  # no error


def test_temp_dir_removed_on_expiry_and_swept_on_any_call():
    clock = Clock()
    f, fac = flow(clock=clock, ttl_seconds=600)
    res = f.start(1, APPLE_ID, PASSWORD)
    d = fac.cls.instances[0].cookie_directory
    clock.t = 601
    f.cancel("other")  # any call sweeps
    assert not os.path.exists(d)
    with pytest.raises(FlowExpired):
        f.verify(1, res.flow_id, "123456")


def test_verify_expired_by_clock():
    clock = Clock()
    f, fac = flow(clock=clock)
    res = f.start(1, APPLE_ID, PASSWORD)
    clock.t = 600.5
    with pytest.raises(FlowExpired):
        f.verify(1, res.flow_id, "123456")
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_flow_bound_to_user():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    with pytest.raises(FlowExpired):
        f.verify(2, res.flow_id, "123456")
    # the owner can still use it (attacker did not destroy it nor burn attempts)
    assert fac.cls.instances[0].attempts == 0
    assert f.verify(1, res.flow_id, "123456")


def test_flow_cap():
    f, fac = flow(max_pending=2)
    f.start(1, APPLE_ID, PASSWORD)
    f.start(1, APPLE_ID, PASSWORD)
    with pytest.raises(TooManyFlows):
        f.start(1, APPLE_ID, PASSWORD)
    assert len(fac.cls.instances) == 2  # never reached Apple


def test_wrong_code_then_cap_destroys_flow():
    f, fac = flow(max_code_attempts=3)
    res = f.start(1, APPLE_ID, PASSWORD)
    d = fac.cls.instances[0].cookie_directory
    for _ in range(3):
        with pytest.raises(InvalidCode):
            f.verify(1, res.flow_id, "000000")
    assert not os.path.exists(d)
    with pytest.raises(FlowExpired):
        f.verify(1, res.flow_id, "123456")
    assert fac.cls.instances[0].attempts == 3


def test_wrong_code_keeps_flow_until_cap():
    f, _ = flow(max_code_attempts=3)
    res = f.start(1, APPLE_ID, PASSWORD)
    with pytest.raises(InvalidCode):
        f.verify(1, res.flow_id, "000000")
    assert f.verify(1, res.flow_id, "123456")


def test_request_2fa_failure_is_non_fatal():
    f, fac = flow(make_factory(requires_2fa=True, request_error=RuntimeError("push already sent")))
    res = f.start(1, APPLE_ID, PASSWORD)
    assert res.status == "needs_2fa"
    assert f.verify(1, res.flow_id, "123456")


def test_hsa2_account_has_both_flags_and_proceeds_to_2fa():
    # pyicloud 2026.11.2: requires_2sa is also True for HSA2 accounts
    f, fac = flow(make_factory(requires_2fa=True, requires_2sa=True))
    res = f.start(1, APPLE_ID, PASSWORD)
    assert res.status == "needs_2fa"
    assert fac.cls.instances[0].requested
    assert f.verify(1, res.flow_id, "123456").dsid == "999"
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_legacy_2sa_only_fails_closed_and_removes_dir():
    f, fac = flow(make_factory(requires_2fa=False, requires_2sa=True))
    with pytest.raises(AppleLoginFailed):
        f.start(1, APPLE_ID, PASSWORD)
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)
    assert not f._flows and f._starting == 0


def test_sweep_does_not_remove_dir_while_verify_in_progress():
    clock = Clock()
    entered, release = threading.Event(), threading.Event()
    fac = make_factory(requires_2fa=True)

    def slow(self, code):
        entered.set()
        assert release.wait(5)
        return code == self.good_code

    fac.cls.validate_2fa_code = slow
    f, _ = flow(fac, clock=clock, ttl_seconds=600)
    res = f.start(1, APPLE_ID, PASSWORD)
    d = fac.cls.instances[0].cookie_directory
    out = []
    t = threading.Thread(target=lambda: out.append(f.verify(1, res.flow_id, "123456")))
    t.start()
    assert entered.wait(5)
    clock.t = 10_000  # flow is now expired
    f.cancel("other")  # triggers a sweep on another thread
    assert os.path.isdir(d)
    release.set()
    t.join(5)
    assert out and not os.path.exists(d)


def test_dir_recreated_by_pyicloud_during_verify_is_removed_after():
    fac = make_factory(requires_2fa=True)

    def recreate(self, code):
        import shutil

        shutil.rmtree(self.cookie_directory)
        os.makedirs(self.cookie_directory)
        open(os.path.join(self.cookie_directory, "session"), "w").write("tok")
        return True

    fac.cls.validate_2fa_code = recreate
    f, _ = flow(fac)
    res = f.start(1, APPLE_ID, PASSWORD)
    f.verify(1, res.flow_id, "123456")
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_mkdtemp_failure_does_not_leak_pending_slot(monkeypatch):
    import tempfile

    f, fac = flow(max_pending=1)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    for _ in range(3):
        with pytest.raises(OSError):
            f.start(1, APPLE_ID, PASSWORD)
    monkeypatch.undo()
    assert f.start(1, APPLE_ID, PASSWORD).status == "needs_2fa"


def test_cancel_user_removes_only_that_users_flows():
    f, fac = flow()
    a = f.start(1, APPLE_ID, PASSWORD)
    b = f.start(2, APPLE_ID, PASSWORD)
    f.cancel_user(1)
    d1, d2 = (i.cookie_directory for i in fac.cls.instances)
    assert not os.path.exists(d1) and os.path.isdir(d2)
    with pytest.raises(FlowExpired):
        f.verify(1, a.flow_id, "123456")
    assert f.verify(2, b.flow_id, "123456")


def test_security_key_cannot_complete():
    f, fac = flow(make_factory(requires_2fa=True, kind="security_key"))
    with pytest.raises(AppleLoginFailed):
        f.start(1, APPLE_ID, PASSWORD)
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_verify_exception_from_pyicloud_fails_closed_and_cleans():
    fac = make_factory(requires_2fa=True)

    def boom(self, code):
        raise RuntimeError(f"boom {code}")

    fac.cls.validate_2fa_code = boom
    f, _ = flow(fac)
    res = f.start(1, APPLE_ID, PASSWORD)
    d = fac.cls.instances[0].cookie_directory
    with pytest.raises(AppleLoginFailed) as ei:
        f.verify(1, res.flow_id, "123456")
    assert "123456" not in str(ei.value)
    assert not os.path.exists(d)


def test_unusable_session_without_premiummail_fails():
    fac = make_factory(requires_2fa=False)
    fac.cls.__init__orig = fac.cls.__init__

    def init(self, *a):
        FakeService.__init__(self, *a)
        self.webservices = {}

    fac.cls.__init__ = init
    f, _ = flow(fac)
    with pytest.raises(AppleLoginFailed):
        f.start(1, APPLE_ID, PASSWORD)
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_password_not_retained_by_flow():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    for v in vars(f).values():
        assert PASSWORD not in repr(v)
    for fl in f._flows.values():
        assert PASSWORD not in repr(vars(fl))


def test_concurrent_verify_one_wins():
    f, _ = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    results = []

    def go():
        try:
            results.append(f.verify(1, res.flow_id, "123456"))
        except FlowExpired:
            results.append("expired")

    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results if r != "expired") == 1
