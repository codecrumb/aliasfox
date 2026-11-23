"""Bitwarden SimpleLogin-compatible endpoint (Task 7)."""
import pytest
from helpers import MultiUserEnv

from aliasfox.apple.hme import HmeError

URL = "/api/alias/random/new"


@pytest.fixture
def env(tmp_path, monkeypatch):
    return MultiUserEnv(tmp_path, monkeypatch, ["a", "b"])


def key_for(env, name, account, scopes=("create",)):
    c, uid = env.sign_in(name)
    if account:
        env.connect(c, account)
    key, _ = env.app.state.api_keys.create(uid, "k", list(scopes))
    return key


def labels(env):
    c, _ = env.sign_in("a")
    return c.get("/api/aliases").json()


def by_hme(env, hme):
    return next(a for a in labels(env) if a["hme"] == hme)


def test_success_201_alias_and_label_from_hostname(env):
    key = key_for(env, "a", "acct-a")
    r = env.anonymous().post(URL + "?hostname=example.com", headers={"Authentication": key}, json={"note": "n"})
    assert r.status_code == 201 and set(r.json()) == {"alias"} and "@" in r.json()["alias"]
    got = by_hme(env, r.json()["alias"])
    assert got["label"] == "example.com" and got["note"] == "n"


def test_authorization_bearer_also_accepted(env):
    key = key_for(env, "a", "acct-a")
    r = env.anonymous().post(URL, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 201


@pytest.mark.parametrize("q", ["", "?hostname=", "?hostname=%20%20"])
def test_default_label_bitwarden_without_hostname(env, q):
    key = key_for(env, "a", "acct-a")
    r = env.anonymous().post(URL + q, headers={"Authentication": key})
    assert r.status_code == 201 and by_hme(env, r.json()["alias"])["label"] == "Bitwarden"


def test_note_truncated_and_bad_json_body_ok(env):
    key = key_for(env, "a", "acct-a")
    h = {"Authentication": key}
    c = env.anonymous()
    r = c.post(URL, headers=h, json={"note": "x" * 5000})
    assert r.status_code == 201 and len(by_hme(env, r.json()["alias"])["note"]) == 2048
    for body in (b"{not json", b"[1,2]", b'"s"', b'{"note": 5}'):
        r = c.post(URL, headers={**h, "Content-Type": "application/json"}, content=body)
        assert r.status_code == 201, body


def test_hostname_truncated_to_256(env):
    key = key_for(env, "a", "acct-a")
    r = env.anonymous().post(URL + "?hostname=" + "h" * 400, headers={"Authentication": key})
    assert r.status_code == 201 and len(by_hme(env, r.json()["alias"])["label"]) == 256


def test_errors_are_error_json(env):
    c = env.anonymous()
    r = c.post(URL, headers={"Authentication": "nope"})
    assert r.status_code == 401 and r.json() == {"error": "Invalid API key"}
    r = c.post(URL)
    assert r.status_code == 401 and r.json() == {"error": "Invalid API key"}

    ro = key_for(env, "a", "acct-a", ["read"])
    r = c.post(URL, headers={"Authentication": ro})
    assert r.status_code == 403 and r.json() == {"error": "This key cannot create addresses"}

    nc = key_for(env, "b", None)
    r = c.post(URL, headers={"Authentication": nc})
    assert r.status_code == 503 and r.json()["error"].startswith("Apple session expired")
    assert "Sign in again at" not in r.json()["error"]  # public_url unset


def test_session_expired_mentions_public_url(env):
    env.settings.public_url = "https://fox.example"
    key = key_for(env, "a", "acct-a")
    del env.world.accounts["acct-a"]
    r = env.anonymous().post(URL, headers={"Authentication": key})
    assert r.status_code == 503
    assert r.json() == {"error": "Apple session expired. Sign in again at https://fox.example"}


def test_rate_limited_429_with_retry_after(env):
    key = key_for(env, "a", "acct-a")
    c = env.anonymous()
    codes = [c.post(URL, headers={"Authentication": key}).status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]
    r = c.post(URL, headers={"Authentication": key})
    assert r.json()["error"].startswith("Too many addresses created. Try again in ")
    assert int(r.headers["retry-after"]) >= 1


def test_apple_error_502_truncated(env, monkeypatch):
    from helpers import FakeHmeClient

    key = key_for(env, "a", "acct-a")

    def boom(self, hme, label, note=""):
        raise HmeError(-1, "z" * 500)

    monkeypatch.setattr(FakeHmeClient, "reserve", boom)
    r = env.anonymous().post(URL, headers={"Authentication": key})
    assert r.status_code == 502 and r.json() == {"error": "iCloud: " + "z" * 200}


def test_failed_auth_flood_429_with_retry_after(env):
    c = env.anonymous()
    r = None
    for _ in range(25):
        r = c.post(URL, headers={"Authentication": "bad"})
    assert r.status_code == 429 and "error" in r.json() and int(r.headers["retry-after"]) >= 1


def test_body_over_64kib_is_413(env):
    key = key_for(env, "a", "acct-a")
    r = env.anonymous().post(URL, headers={"Authentication": key}, content=b"x" * 70_000)
    assert r.status_code == 413


def test_cookies_ignored_and_no_redirect(env):
    c, _ = env.sign_in("a")
    env.connect(c, "acct-a")
    r = c.post(URL, follow_redirects=False)  # cookie session, no key
    assert r.status_code == 401
    assert env.anonymous().post(URL + "/", follow_redirects=False).status_code in (404, 405)


def test_regenerate_costs_one_slot_each(env):
    key = key_for(env, "a", "acct-a")
    c = env.anonymous()
    before = len(labels(env))
    for _ in range(2):
        assert c.post(URL, headers={"Authentication": key}).status_code == 201
    assert len(labels(env)) == before + 2
