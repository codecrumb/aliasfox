import getpass

import pytest

import aliasfox.__main__ as entry
from aliasfox.config import Settings
from aliasfox.crypto import verify_password

GOOD = "correct-Horse9!battery"


def feed(monkeypatch, *answers):
    it = iter(answers)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(it))


def test_hash_password_prints_only_verifiable_hash(monkeypatch, capsys):
    feed(monkeypatch, GOOD, GOOD)
    assert entry.main(["hash-password"]) == 0
    out = capsys.readouterr()
    assert out.out.strip().startswith("$argon2id$")
    assert verify_password(out.out.strip(), GOOD)
    assert GOOD not in out.out + out.err


@pytest.mark.parametrize(
    "pw",
    ["short1!", "nodigitsorsymbols", "nosymbol123456", "no-digits-here!!"],
)
def test_hash_password_policy_rejects(monkeypatch, capsys, pw):
    feed(monkeypatch, pw, pw)
    assert entry.main(["hash-password"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert pw not in out.err


def test_hash_password_mismatch(monkeypatch, capsys):
    feed(monkeypatch, GOOD, GOOD + "x")
    assert entry.main(["hash-password"]) == 1
    assert capsys.readouterr().out == ""


def test_gen_secret(capsys):
    assert entry.main(["gen-secret"]) == 0
    a = capsys.readouterr().out.strip()
    assert len(a) >= 64
    entry.main(["gen-secret"])
    assert capsys.readouterr().out.strip() != a


def test_default_runs_uvicorn_with_settings(monkeypatch):
    captured = {}
    monkeypatch.setattr(entry, "get_settings", lambda: Settings(secret_key="k" * 32, host="127.0.0.1", port=9123))
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw))
    monkeypatch.setattr(entry, "create_app", lambda s: ("APP", s.port))
    assert entry.main([]) == 0
    assert captured["app"] == ("APP", 9123)
    assert captured["host"] == "127.0.0.1" and captured["port"] == 9123
    assert captured["proxy_headers"] is False
    assert captured["access_log"] is False
    assert captured["log_level"] == "info"
    assert captured.get("workers", 1) == 1


def test_public_bind_guard_applies_before_server(monkeypatch):
    from aliasfox.config import ConfigError

    monkeypatch.setattr(entry, "get_settings", lambda: Settings(secret_key="k" * 32, host="0.0.0.0"))
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: pytest.fail("server started"))
    with pytest.raises(ConfigError):
        entry.main([])


def test_unknown_command(capsys):
    with pytest.raises(SystemExit):
        entry.main(["bogus"])
