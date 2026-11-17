"""Shared test helpers."""
import copy

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from aliasfox.apple.hme import Alias, HmeError, SessionExpired
from aliasfox.config import Settings
from aliasfox.keys import KeyRing
from aliasfox.neon_jwt import Identity, TokenRejected
from aliasfox.sessions import AppleSessionState, SessionStore
from aliasfox.users import UserDirectory

TEST_SECRET = "k" * 32


def make_store(engine: Engine, secret: str = TEST_SECRET) -> tuple[SessionStore, UserDirectory]:
    """A SessionStore and UserDirectory over `engine` (already init_db'd), with the
    single-mode user (id 1) created and keyed, as create_app does in single mode."""
    keyring = KeyRing(secret)
    users = UserDirectory(engine, keyring)
    users.ensure_single_user()
    return SessionStore(engine, keyring), users


# --- multi mode: fake Neon verifier ---------------------------------------------------

MULTI_SECRET = "multi-user-test-secret-0123456789abcdef"
MULTI_BASE = "https://aliasfox.test"
MULTI_CONTACT = "operator@example.com"
APPLE_WS_URL = "https://p1-maildomainws.icloud.com"
APPLE_CODE = "123456"


class FakeNeonVerifier:
    """token -> Identity; anything else is rejected like the real verifier."""

    def __init__(self, identities=None):
        self.identities = dict(identities or {})

    def verify(self, token):
        if not isinstance(token, str) or token not in self.identities:
            raise TokenRejected()
        return self.identities[token]


def neon_identity(sub, email="person@example.com", verified=True):
    return Identity(subject=sub, email=email, email_verified=verified)


def multi_mode_settings(tmp_path, **kw):
    kw.setdefault("secret_key", MULTI_SECRET)
    kw.setdefault("database_url", f"sqlite:///{tmp_path}/multi.db")
    kw.setdefault("contact_email", MULTI_CONTACT)
    return Settings(
        aliasfox_mode="multi",
        data_dir=tmp_path,
        neon_auth_base_url="https://auth.example/neondb/auth",
        neon_auth_jwks_url="https://auth.example/neondb/auth/.well-known/jwks.json",
        trusted_proxy=True,
        **kw,
    )


# --- fake Apple: one in-memory "iCloud" with several accounts ------------------------


def apple_state(account: str) -> AppleSessionState:
    """The Apple session for `account`: the account name is the session cookie, so a
    client built from the wrong user's session acts on (and shows) the wrong account."""
    return AppleSessionState(
        cookies={"acct": account}, webservices_url=APPLE_WS_URL, dsid=account
    )


class FakeAppleWorld:
    """Hide My Email accounts keyed by name. Alias ids and addresses embed the
    account name, so any cross-user leak is visible in the response text. Every
    client call is logged as (account, op)."""

    def __init__(self):
        self.accounts: dict[str, dict] = {}
        self.calls: list[tuple[str | None, str]] = []

    def add_account(self, name: str, n: int = 2) -> None:
        aliases = {}
        for i in range(n):
            aid = f"{name}-id-{i}"
            aliases[aid] = Alias(
                anonymous_id=aid,
                hme=f"{name}-{i}@privaterelay.appleid.com",
                label=f"{name} label {i}",
                note=f"{name} note {i}",
                is_active=True,
                forward_to=f"{name}@real.example",
                origin="ON_DEMAND",
                domain="",
                created_ms=1_700_000_000_000 + i,
                raw={},
            )
        self.accounts[name] = {"forward_to": f"{name}@real.example", "aliases": aliases, "gen": 0}

    def snapshot(self, name: str) -> dict:
        acct = self.accounts[name]
        return {
            "forward_to": acct["forward_to"],
            "aliases": {k: copy.copy(v.__dict__) for k, v in acct["aliases"].items()},
        }

    def ops_on(self, name: str) -> list[str]:
        return [op for acct, op in self.calls if acct == name]

    def client_factory(self):
        def factory(state, *, on_state_change=None):
            return FakeHmeClient(self, state)

        return factory


class FakeHmeClient:
    def __init__(self, world: FakeAppleWorld, state: AppleSessionState):
        self.world = world
        self.account = state.cookies.get("acct")

    def _acct(self, op: str) -> dict:
        self.world.calls.append((self.account, op))
        acct = self.world.accounts.get(self.account)
        if acct is None:
            raise SessionExpired(401, "unknown session")
        return acct

    def _alias(self, acct: dict, anonymous_id: str) -> Alias:
        try:
            return acct["aliases"][anonymous_id]
        except KeyError:
            raise HmeError("not_found", "no such alias in this account") from None

    def list(self):
        return [copy.copy(a) for a in self._acct("list")["aliases"].values()]

    def generate(self):
        acct = self._acct("generate")
        acct["gen"] += 1
        return f"{self.account}-gen-{acct['gen']}@privaterelay.appleid.com"

    def reserve(self, hme, label, note=""):
        acct = self._acct("reserve")
        aid = f"{self.account}-id-r{len(acct['aliases'])}"
        alias = Alias(aid, hme, label, note, True, acct["forward_to"], "ON_DEMAND", "", None, {})
        acct["aliases"][aid] = alias
        return copy.copy(alias)

    def update_metadata(self, anonymous_id, label, note=None):
        alias = self._alias(self._acct("update_metadata"), anonymous_id)
        alias.label = label
        if note is not None:
            alias.note = note

    def set_active(self, anonymous_id, active):
        self._alias(self._acct("set_active"), anonymous_id).is_active = active

    def delete(self, anonymous_id):
        acct = self._acct("delete")
        self._alias(acct, anonymous_id)
        del acct["aliases"][anonymous_id]

    def update_forward_to(self, address):
        self._acct("update_forward_to")["forward_to"] = address

    def close(self):
        pass


# --- fake pyicloud for LoginFlow: the Apple ID becomes the account name ----------------


class _FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class _FakeSession:
    def __init__(self, account):
        self.cookies = [_FakeCookie("acct", account)]
        self.data = {}


class FakeLoginApi:
    """Always needs 2FA; APPLE_CODE is the only valid code."""

    requires_2fa = True
    requires_2sa = False
    two_factor_delivery_method = "trusted_device"
    is_trusted_session = True

    def __init__(self, apple_id):
        self.apple_id = apple_id
        self.session = _FakeSession(apple_id)
        self.data = {"dsInfo": {"dsid": apple_id}}
        self.webservices = {"premiummailsettings": {"url": APPLE_WS_URL}}
        self.code_requests = 0
        self.validations = 0

    def request_2fa_code(self):
        self.code_requests += 1

    def validate_2fa_code(self, code):
        self.validations += 1
        return code == APPLE_CODE


def fake_login_factory(made: list):
    def factory(apple_id, password, *, cookie_directory, china_mainland=False):
        api = FakeLoginApi(apple_id)
        made.append(api)
        return api

    return factory


# --- a whole multi-mode app with two or more users -------------------------------------


class MultiUserEnv:
    """create_app in multi mode over a real SQLite file. Only the Neon verifier,
    Apple's HME service and pyicloud are faked."""

    def __init__(self, tmp_path, monkeypatch, subjects, *, dist_dir=None, secret=MULTI_SECRET):
        import aliasfox.api as api_mod
        import aliasfox.main as main_mod
        from aliasfox.apple.login import LoginFlow
        from aliasfox.db import make_engine
        from aliasfox.service import AliasService

        self.verifier = FakeNeonVerifier({f"tok-{s}": neon_identity(f"sub-{s}") for s in subjects})
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        monkeypatch.setattr(main_mod, "NeonTokenVerifier", lambda **_: self.verifier)
        # The cookie text names the Apple account it connects.
        monkeypatch.setattr(api_mod, "connect_with_cookie", lambda text, **_: apple_state(text))
        self.settings = multi_mode_settings(tmp_path, signups_enabled=True, secret_key=secret)
        self.engine = make_engine(self.settings)
        self.store = SessionStore(self.engine, KeyRing(secret))
        self.world = FakeAppleWorld()
        self.login_apis: list[FakeLoginApi] = []
        self.flow = LoginFlow(service_factory=fake_login_factory(self.login_apis))
        self.service = AliasService(self.store, client_factory=self.world.client_factory())
        self.app = main_mod.create_app(
            self.settings, service=self.service, store=self.store,
            login_flow=self.flow, dist_dir=dist_dir,
        )
        self._ip = 0

    @property
    def users(self) -> UserDirectory:
        return self.app.state.epochs

    def anonymous(self) -> TestClient:
        """A client with its own cookie jar and its own client IP."""
        self._ip += 1
        return TestClient(
            self.app, base_url=MULTI_BASE, headers={"X-Forwarded-For": f"198.51.100.{self._ip}"}
        )

    def sign_in(self, name: str) -> tuple[TestClient, int]:
        c = self.anonymous()
        r = c.post("/api/auth/exchange", json={"token": f"tok-{name}"})
        assert r.status_code == 200, r.text
        return c, self.users.find_by_subject(f"sub-{name}")

    def resign_in(self, c: TestClient, name: str) -> None:
        r = c.post("/api/auth/exchange", json={"token": f"tok-{name}"})
        assert r.status_code == 200, r.text

    def connect(self, c: TestClient, account: str) -> None:
        if account not in self.world.accounts:
            self.world.add_account(account)
        r = c.post("/api/connect/cookie", json={"cookie": account})
        assert r.status_code == 200, r.text

    def raw(self, sql: str, **params):
        with self.engine.begin() as conn:
            return conn.execute(text(sql), params)

    def blob(self, uid: int) -> bytes | None:
        return self.raw("select blob from apple_sessions where user_id=:u", u=uid).scalar()

    def wrapped_key(self, uid: int) -> bytes | None:
        return self.raw("select wrapped_key from users where id=:u", u=uid).scalar()
