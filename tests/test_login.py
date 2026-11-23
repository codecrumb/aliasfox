import os
import threading

import pytest

from aliasfox.apple.connect import AppleUnreachable
from aliasfox.apple.login import (
    AppleLoginUnsupported,
    AppleLoginFailed,
    FlowExpired,
    InvalidCode,
    LoginFlow,
    TooManyFlows,
    TooManyResends,
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
    trust_error: Exception | None = None
    gate: threading.Event | None = None  # request_2fa_code blocks until set
    entered: threading.Event | None = None  # set when request_2fa_code is called

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
        self.request_count = 0
        self.attempts = 0
        self.bridge = False
        self.trust_calls = 0
        self.is_trusted_session = False
        self.instances.append(self)

    def request_2fa_code(self):
        if self.entered:
            self.entered.set()
        if self.gate:
            assert self.gate.wait(5)
        self.requested = True
        self.request_count += 1
        self.bridge = True  # Apple pushed the code even when the HTTP call then errors
        if self.request_error:
            raise self.request_error

    def validate_2fa_code(self, code):
        # Mimics pyicloud: the trusted-device bridge state is consumed by every
        # validate call, so a retry needs a fresh request_2fa_code().
        self.attempts += 1
        armed, self.bridge = self.bridge, False
        return armed and code == self.good_code

    def trust_session(self):
        self.trust_calls += 1
        if self.trust_error:
            raise self.trust_error
        return True


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
    assert os.path.isdir(inst.cookie_directory)
    assert oct(os.stat(inst.cookie_directory).st_mode & 0o777) == "0o700"
    st = f.verify(1, res.flow_id, "123456")
    assert inst.requested
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


def test_network_error_at_start_is_unreachable_not_login_failed():
    f, _ = flow(make_factory(auth_error=ConnectionError("dns")))
    with pytest.raises(AppleUnreachable):
        f.start(1, APPLE_ID, PASSWORD)


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
    f.start(2, APPLE_ID, PASSWORD)
    with pytest.raises(TooManyFlows):
        f.start(3, APPLE_ID, PASSWORD)
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
    f.resend(1, res.flow_id)  # the flow survives a wrong code; a retry needs a fresh code
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
    assert f.verify(1, res.flow_id, "123456").dsid == "999"
    assert fac.cls.instances[0].requested
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_legacy_2sa_only_fails_closed_and_removes_dir():
    f, fac = flow(make_factory(requires_2fa=False, requires_2sa=True))
    with pytest.raises(AppleLoginUnsupported):
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


def _no_premiummail_factory(available):
    fac = make_factory(requires_2fa=False)

    def init(self, *a):
        FakeService.__init__(self, *a)
        self.webservices = {}
        self.data = {"dsInfo": {"dsid": "999", "isHideMyEmailFeatureAvailable": available}}

    fac.cls.__init__ = init
    return fac


def test_no_icloud_plus_login_is_kept_with_flag_false():
    f, fac = flow(_no_premiummail_factory(False))
    res = f.start(1, APPLE_ID, PASSWORD)
    assert res.status == "ok"
    assert res.state.webservices_url == ""
    assert res.state.hme_available is False
    assert res.state.dsid == "999"
    assert not os.path.exists(fac.cls.instances[0].cookie_directory)


def test_missing_premiummail_with_flag_true_still_fails():
    f, _ = flow(_no_premiummail_factory(True))
    with pytest.raises(AppleLoginFailed):
        f.start(1, APPLE_ID, PASSWORD)


def test_flag_true_is_stored_on_normal_login():
    fac = make_factory(requires_2fa=False)

    def init(self, *a):
        FakeService.__init__(self, *a)
        self.data = {"dsInfo": {"dsid": "999", "isHideMyEmailFeatureAvailable": True}}

    fac.cls.__init__ = init
    f, _ = flow(fac)
    assert f.start(1, APPLE_ID, PASSWORD).state.hme_available is True


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


# --- B3: wrong code retry ----------------------------------------------------------


def test_fake_mimics_pyicloud_retry_needs_fresh_request():
    svc = make_factory(requires_2fa=True).cls("a", "p", os.getcwd(), False)
    svc.request_2fa_code()
    assert svc.validate_2fa_code("000000") is False
    assert svc.validate_2fa_code("123456") is False  # bridge consumed
    svc.request_2fa_code()
    assert svc.validate_2fa_code("123456") is True


def test_wrong_code_does_not_trigger_a_new_prompt():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    inst = fac.cls.instances[0]
    with pytest.raises(InvalidCode):
        f.verify(1, res.flow_id, "000000")
    assert inst.request_count == 1  # only the code request from start()


def test_attempt_cap_still_five_by_default_and_never_rerequests():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    inst = fac.cls.instances[0]
    for _ in range(5):
        with pytest.raises(InvalidCode):
            f.verify(1, res.flow_id, "000000")
    with pytest.raises(FlowExpired):
        f.verify(1, res.flow_id, "123456")
    assert inst.request_count == 1


# --- resend + background code request ---------------------------------------------------


def test_start_returns_before_the_code_request_finishes():
    gate, entered = threading.Event(), threading.Event()
    f, fac = flow(make_factory(requires_2fa=True, gate=gate, entered=entered))
    res = f.start(1, APPLE_ID, PASSWORD)
    assert res.status == "needs_2fa" and res.flow_id
    inst = fac.cls.instances[0]
    assert entered.wait(5)  # the request runs in the background...
    assert inst.request_count == 0  # ...and has not finished
    gate.set()
    assert f.verify(1, res.flow_id, "123456")
    assert inst.request_count == 1


def test_verify_waits_for_the_pending_code_request():
    gate, entered = threading.Event(), threading.Event()
    f, _ = flow(make_factory(requires_2fa=True, gate=gate, entered=entered))
    res = f.start(1, APPLE_ID, PASSWORD)
    assert entered.wait(5)
    out: list = []

    def go():
        try:
            out.append(f.verify(1, res.flow_id, "123456"))
        except Exception as exc:  # noqa: BLE001
            out.append(exc)

    t = threading.Thread(target=go)
    t.start()
    t.join(0.3)
    assert t.is_alive() and out == []  # still waiting for the request
    gate.set()
    t.join(5)
    assert len(out) == 1 and out[0].dsid == "999"  # not InvalidCode: the code was requested first


def test_request_failure_in_the_background_is_non_fatal_and_leaks_nothing(caplog):
    caplog.set_level("DEBUG")
    f, _ = flow(make_factory(requires_2fa=True, request_error=RuntimeError("push " + PASSWORD)))
    res = f.start(1, APPLE_ID, PASSWORD)
    assert f.verify(1, res.flow_id, "123456")
    assert PASSWORD not in caplog.text


def test_resend_requests_a_fresh_code_so_retry_succeeds():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    inst = fac.cls.instances[0]
    with pytest.raises(InvalidCode):
        f.verify(1, res.flow_id, "000000")
    f.resend(1, res.flow_id)
    assert inst.request_count == 2
    assert f.verify(1, res.flow_id, "123456").webservices_url.startswith("https://p1-")


def test_resend_for_unknown_or_foreign_flow_is_expired():
    f, _ = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    with pytest.raises(FlowExpired):
        f.resend(1, "nope")
    with pytest.raises(FlowExpired):
        f.resend(2, res.flow_id)


def test_resend_after_expiry_is_expired():
    clock = Clock()
    f, _ = flow(clock=clock, ttl_seconds=10)
    res = f.start(1, APPLE_ID, PASSWORD)
    clock.t = 11
    with pytest.raises(FlowExpired):
        f.resend(1, res.flow_id)


def test_resend_is_capped_per_flow_and_keeps_the_flow():
    f, fac = flow(max_resends=2)
    res = f.start(1, APPLE_ID, PASSWORD)
    f.resend(1, res.flow_id)
    f.resend(1, res.flow_id)
    with pytest.raises(TooManyResends):
        f.resend(1, res.flow_id)
    assert fac.cls.instances[0].request_count == 3  # start + 2 resends
    assert f.verify(1, res.flow_id, "123456")  # the flow is still usable


def test_resend_failure_is_non_fatal():
    f, _ = flow(make_factory(requires_2fa=True, request_error=RuntimeError("push " + PASSWORD)))
    res = f.start(1, APPLE_ID, PASSWORD)
    f.resend(1, res.flow_id)  # Apple may have pushed it anyway: no exception
    assert f.verify(1, res.flow_id, "123456")


def test_trust_session_failure_still_completes_login():
    f, fac = flow(make_factory(requires_2fa=True, trust_error=RuntimeError("boom " + PASSWORD)))
    res = f.start(1, APPLE_ID, PASSWORD)
    st = f.verify(1, res.flow_id, "123456")
    assert st.webservices_url
    assert fac.cls.instances[0].trust_calls == 1


def test_trust_session_called_after_correct_code():
    f, fac = flow()
    res = f.start(1, APPLE_ID, PASSWORD)
    f.verify(1, res.flow_id, "123456")
    assert fac.cls.instances[0].trust_calls == 1


# --- B4: per-user flow cap ---------------------------------------------------------


def test_start_cancels_users_existing_flow_first():
    f, fac = flow()
    first = f.start(1, APPLE_ID, PASSWORD)
    other = f.start(2, APPLE_ID, PASSWORD)
    d_first = fac.cls.instances[0].cookie_directory
    second = f.start(1, APPLE_ID, PASSWORD)
    assert not os.path.exists(d_first)
    with pytest.raises(FlowExpired):
        f.verify(1, first.flow_id, "123456")
    assert f.verify(2, other.flow_id, "123456")  # other users untouched
    assert f.verify(1, second.flow_id, "123456")


def test_per_user_cap_is_one_pending_flow():
    f, fac = flow()
    for _ in range(4):
        f.start(1, APPLE_ID, PASSWORD)
    pending = [i for i in fac.cls.instances if os.path.isdir(i.cookie_directory)]
    assert len(pending) == 1


def test_locked_account_is_reported_apart_from_bad_credentials():
    from aliasfox.apple.login import AppleAccountLocked

    class PyiCloudAccountLockedException(Exception):
        pass

    def factory(*a, **k):
        raise PyiCloudAccountLockedException("locked")

    f, _ = flow(factory)
    with pytest.raises(AppleAccountLocked):
        f.start(1, APPLE_ID, PASSWORD)
