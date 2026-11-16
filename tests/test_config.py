import pytest

from aliasfox.config import ConfigError, Settings, validate_startup


def make(**kw):
    kw.setdefault("secret_key", "s3cret")
    return Settings(**kw)


def test_missing_secret_key_refuses_start():
    with pytest.raises(ConfigError):
        validate_startup(make(secret_key=None))


def test_public_bind_without_auth_refused():
    with pytest.raises(ConfigError):
        validate_startup(make(host="0.0.0.0", auth_enabled=False))


def test_public_bind_allowed_with_escape_hatch():
    validate_startup(make(host="0.0.0.0", allow_unauthenticated_network=True))


def test_public_bind_allowed_with_auth():
    validate_startup(make(host="0.0.0.0", auth_enabled=True))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_bind_allowed_without_auth(host):
    validate_startup(make(host=host))


def test_env_aliases(monkeypatch):
    monkeypatch.setenv("ALIASFOX_SECRET_KEY", "k")
    monkeypatch.setenv("ALIASFOX_MODE", "multi")
    monkeypatch.setenv("PORT", "9000")
    s = Settings()
    assert (s.secret_key, s.aliasfox_mode, s.port) == ("k", "multi", 9000)
