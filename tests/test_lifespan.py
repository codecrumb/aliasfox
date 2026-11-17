import asyncio
import logging
import os
import time

import pytest
from fastapi.testclient import TestClient
from helpers import make_store
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

import aliasfox.main as main_mod
from aliasfox.apple.connect import InvalidCookie
from aliasfox.apple.login import LoginFlow
from aliasfox.config import Settings
from aliasfox.db import init_db

SECRET_COOKIE = "X-APPLE-WEBAUTH-USER=TOPSECRETCOOKIETEXT"


class FakeService:
    async def is_connected(self, user_id):
        return True

    async def forget(self, user_id):
        pass


def new_store():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    init_db(eng)
    return make_store(eng)[0]


def make_app(cookie=None, flow=None):
    settings = Settings(secret_key="unit-test-secret-key-0123456789abcdef", icloud_cookie=cookie)
    return main_mod.create_app(
        settings, service=FakeService(), store=new_store(), login_flow=flow or LoginFlow()
    )


@pytest.fixture
def tmp_tempdir(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    return tmp_path


def test_seed_runs_once_when_env_cookie_set(monkeypatch, tmp_tempdir):
    calls = []
    monkeypatch.setattr(main_mod, "seed_from_env", lambda s, st: calls.append(s.icloud_cookie))
    with TestClient(make_app(SECRET_COOKIE)):
        pass
    assert calls == [SECRET_COOKIE]


def test_no_seed_without_cookie(monkeypatch, tmp_tempdir):
    calls = []
    monkeypatch.setattr(main_mod, "seed_from_env", lambda s, st: calls.append(1))
    with TestClient(make_app(None)):
        pass
    assert calls == []


@pytest.mark.parametrize("exc", [InvalidCookie("bad " + SECRET_COOKIE), RuntimeError(SECRET_COOKIE)])
def test_bad_cookie_does_not_crash_and_logs_only_class(monkeypatch, caplog, tmp_tempdir, exc):
    def boom(s, st):
        raise exc

    monkeypatch.setattr(main_mod, "seed_from_env", boom)
    with caplog.at_level(logging.DEBUG):
        with TestClient(make_app(SECRET_COOKIE)) as client:
            assert client.get("/healthz").status_code == 200
    assert "TOPSECRETCOOKIETEXT" not in caplog.text
    assert type(exc).__name__ in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_stale_login_dirs_removed_only_old_prefixed(monkeypatch, tmp_tempdir):
    old = tmp_tempdir / "aliasfox-login-old"
    fresh = tmp_tempdir / "aliasfox-login-fresh"
    other = tmp_tempdir / "other-old"
    for d in (old, fresh, other):
        d.mkdir()
        (d / "f").write_text("x")
    past = time.time() - 3600
    for d in (old, other):
        os.utime(d, (past, past))
    with TestClient(make_app()):
        assert not old.exists()
        assert fresh.exists()
        assert other.exists()


@pytest.mark.parametrize("name", ["pyicloud", "httpx"])
def test_noisy_loggers_warning(tmp_tempdir, name):
    logging.getLogger(name).setLevel(logging.DEBUG)
    with TestClient(make_app()):
        assert logging.getLogger(name).level == logging.WARNING


def test_shutdown_removes_pending_flow_dirs(tmp_tempdir):
    class Api:
        requires_2fa = True
        requires_2sa = False

        def request_2fa_code(self):
            pass

    flow = LoginFlow(service_factory=lambda *a, **k: Api())
    with TestClient(make_app(flow=flow)):
        flow.start(1, "a@b.c", "pw")
        dirs = list(tmp_tempdir.glob("aliasfox-login-*"))
        assert len(dirs) == 1
    assert list(tmp_tempdir.glob("aliasfox-login-*")) == []
    assert flow._flows == {}


def test_sweep_task_runs_periodically_and_is_cancelled(monkeypatch, tmp_tempdir):
    monkeypatch.setattr(main_mod, "SWEEP_INTERVAL_SECONDS", 0.01)
    swept = []

    class F(LoginFlow):
        def sweep(self):
            swept.append(1)
            super().sweep()

    with TestClient(make_app(flow=F())):
        deadline = time.time() + 2
        while len(swept) < 2 and time.time() < deadline:
            time.sleep(0.01)
    assert len(swept) >= 2
    n = len(swept)
    time.sleep(0.05)
    assert len(swept) == n  # cancelled on shutdown


def test_sweep_expires_idle_flow(tmp_tempdir):
    now = [0.0]

    class Api:
        requires_2fa = True
        requires_2sa = False

        def request_2fa_code(self):
            pass

    flow = LoginFlow(service_factory=lambda *a, **k: Api(), clock=lambda: now[0], ttl_seconds=10)
    flow.start(1, "a@b.c", "pw")
    now[0] = 11
    flow.sweep()
    assert list(tmp_tempdir.glob("aliasfox-login-*")) == []
