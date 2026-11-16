import pytest

from aliasfox.config import ConfigError, Settings, validate_startup
from aliasfox.crypto import hash_password

KEY = "k" * 32
HASH = hash_password("pw-for-config-tests")


def make(**kw):
    kw.setdefault("secret_key", KEY)
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
    validate_startup(make(host="0.0.0.0", auth_enabled=True, password_hash=HASH))


@pytest.mark.parametrize("pw", [None, ""])
def test_auth_enabled_requires_password_hash(pw):
    with pytest.raises(ConfigError):
        validate_startup(make(auth_enabled=True, password_hash=pw))


def test_auth_enabled_with_password_hash_ok():
    validate_startup(make(auth_enabled=True, password_hash=HASH))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_local_bind_allowed_without_auth(host):
    validate_startup(make(host=host))


def test_env_aliases(monkeypatch):
    monkeypatch.setenv("ALIASFOX_SECRET_KEY", KEY)
    monkeypatch.setenv("ALIASFOX_MODE", "multi")
    monkeypatch.setenv("PORT", "9000")
    s = Settings()
    assert (s.secret_key, s.aliasfox_mode, s.port) == (KEY, "multi", 9000)


def test_secrets_not_in_repr():
    s = make(secret_key="KEYVALUE1", password_hash="HASHVALUE2", icloud_cookie="COOKIEVALUE3")
    text = repr(s)
    for v in ("KEYVALUE1", "HASHVALUE2", "COOKIEVALUE3"):
        assert v not in text


def test_short_secret_key_refused_without_echo():
    short = "SHORTSECRET-31-chars-abcdefghij"
    assert len(short) == 31
    with pytest.raises(ConfigError) as ei:
        validate_startup(make(secret_key=short))
    assert short not in str(ei.value)
    validate_startup(make(secret_key="k" * 32))


def test_multi_mode_refused_until_plan_3():
    with pytest.raises(ConfigError, match="multi mode is not implemented yet"):
        validate_startup(make(aliasfox_mode="multi"))
    validate_startup(make(aliasfox_mode="single"))


@pytest.mark.parametrize(
    "bad",
    ["xyz", "plaintext-password", "$argon2id$v=19$m=65536,t=3,p=4$tooshort", "$2b$12$abcdefghijk"],
)
def test_auth_enabled_requires_parsable_argon2_hash(bad):
    with pytest.raises(ConfigError) as ei:
        validate_startup(make(auth_enabled=True, password_hash=bad))
    msg = str(ei.value)
    assert "not a valid Argon2 hash" in msg
    assert "quote it" in msg
    assert bad not in msg


def test_dollar_mangled_hash_is_caught():
    # what an unquoted .env interpolation typically leaves behind
    mangled = HASH.replace("$argon2id", "").replace("$", "")
    with pytest.raises(ConfigError):
        validate_startup(make(auth_enabled=True, password_hash=mangled))


def test_hash_not_checked_when_auth_disabled():
    validate_startup(make(auth_enabled=False, password_hash="whatever"))


def test_lowercase_env_names_no_longer_populate_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("SECRET_KEY", "PASSWORD_HASH", "MODE", "ALLOW_UNAUTHENTICATED_NETWORK"):
        monkeypatch.setenv(name, "injected-" + name)
    s = Settings()
    assert s.secret_key is None
    assert s.password_hash is None
    assert s.aliasfox_mode == "single"
    assert s.allow_unauthenticated_network is False
    # HOST/PORT/DATA_DIR/DATABASE_URL are the real env names (the Dockerfile sets HOST)
    monkeypatch.setenv("HOST", "0.0.0.0")
    assert Settings().host == "0.0.0.0"


def test_keyword_construction_still_works():
    s = Settings(secret_key=KEY, password_hash="x", host="1.2.3.4", database_url="sqlite://")
    assert (s.secret_key, s.password_hash, s.host) == (KEY, "x", "1.2.3.4")


def test_database_url_not_in_repr():
    s = make(database_url="postgresql://user:DBPASSWORD@db/x")
    assert "DBPASSWORD" not in repr(s)
    assert "DBPASSWORD" not in str(s)
