"""Apple password + 2FA login through pyicloud, in two steps (start / verify).

Security notes:
- The Apple ID, password, 2FA code and cookies are never logged and never put
  in exception messages; we log only exception class names.
- Each flow gets its own 0700 temp cookie directory (never pyicloud's default
  /tmp location, never the keyring); it is deleted when the flow completes,
  fails, is cancelled or expires.
- Our code never stores the password: it is handed straight to pyicloud. pyicloud
  itself keeps it in memory while a flow is pending (at most `ttl_seconds`); the
  service object is discarded when the flow ends.
"""

import logging
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aliasfox.sessions import AppleSessionState

log = logging.getLogger(__name__)


class AppleLoginFailed(Exception):
    """Apple login failed. Fixed message: never carries Apple/pyicloud text."""

    def __init__(self) -> None:
        super().__init__("apple login failed")


class InvalidCode(Exception):
    def __init__(self) -> None:
        super().__init__("invalid verification code")


class FlowExpired(Exception):
    def __init__(self) -> None:
        super().__init__("login flow expired or unknown")


class TooManyFlows(Exception):
    def __init__(self) -> None:
        super().__init__("too many pending login flows")


@dataclass
class LoginResult:
    status: Literal["ok", "needs_2fa"]
    flow_id: str | None = None
    state: AppleSessionState | None = None


@dataclass
class _Flow:
    user_id: int
    service: Any
    directory: str
    expires: float
    attempts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def default_service_factory(apple_id, password, *, cookie_directory, china_mainland=False):
    from pyicloud import PyiCloudService

    return PyiCloudService(
        apple_id,
        password,
        cookie_directory=cookie_directory,
        china_mainland=china_mainland,
    )


def _rmdir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _build_state(api) -> AppleSessionState:
    cookies = {str(c.name): str(c.value) for c in api.session.cookies}
    ws = api.webservices
    entry = ws.get("premiummailsettings") if isinstance(ws, dict) else None
    url = entry.get("url") if isinstance(entry, dict) else None
    if not isinstance(url, str) or not url:
        raise AppleLoginFailed()
    data = getattr(api, "data", None)
    ds = data.get("dsInfo") if isinstance(data, dict) else None
    dsid = ds.get("dsid") if isinstance(ds, dict) else None
    extra: dict = {}
    sdata = getattr(api.session, "data", None)
    if isinstance(sdata, dict) and sdata.get("trust_token"):
        extra["trust_token"] = sdata["trust_token"]
    return AppleSessionState(
        cookies=cookies,
        webservices_url=url,
        dsid=str(dsid) if dsid is not None else None,
        extra=extra,
    )


class LoginFlow:
    def __init__(
        self,
        *,
        service_factory: Callable[..., Any] = default_service_factory,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 600,
        max_pending: int = 20,
        max_code_attempts: int = 5,
    ) -> None:
        self._factory = service_factory
        self._clock = clock
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._max_attempts = max_code_attempts
        self._lock = threading.Lock()
        self._flows: dict[str, _Flow] = {}
        self._starting = 0  # slots reserved by in-progress start() calls

    def _sweep(self) -> None:
        now = self._clock()
        with self._lock:
            dead = [k for k, f in self._flows.items() if f.expires <= now]
            flows = [self._flows.pop(k) for k in dead]
        for f in flows:
            _rmdir(f.directory)
            f.service = None

    def _drop(self, flow_id: str, flow: _Flow) -> None:
        with self._lock:
            if self._flows.get(flow_id) is flow:
                del self._flows[flow_id]
        _rmdir(flow.directory)
        flow.service = None

    def start(
        self, user_id: int, apple_id: str, password: str, *, china: bool = False
    ) -> LoginResult:
        self._sweep()
        with self._lock:
            if len(self._flows) + self._starting >= self._max_pending:
                raise TooManyFlows()
            self._starting += 1
        directory = tempfile.mkdtemp(prefix="aliasfox-login-")  # mode 0700
        keep = False
        try:
            try:
                api = self._factory(
                    apple_id, password, cookie_directory=directory, china_mainland=china
                )
            except Exception as exc:
                log.warning("apple login failed: %s", type(exc).__name__)
                raise AppleLoginFailed() from None
            finally:
                password = None  # drop our reference; pyicloud holds its own copy
            try:
                if not (api.requires_2fa or api.requires_2sa):
                    return LoginResult("ok", None, _build_state(api))
                if api.requires_2sa or getattr(api, "two_factor_delivery_method", "") == "security_key":
                    raise AppleLoginFailed()  # legacy 2SA / hardware key unsupported
                try:
                    api.request_2fa_code()
                except Exception as exc:  # Apple may have pushed the code anyway
                    log.info("request_2fa_code failed: %s", type(exc).__name__)
                flow_id = secrets.token_urlsafe(32)
                flow = _Flow(user_id, api, directory, self._clock() + self._ttl)
                with self._lock:
                    self._flows[flow_id] = flow
                keep = True
                return LoginResult("needs_2fa", flow_id, None)
            except AppleLoginFailed:
                raise
            except Exception as exc:
                log.warning("apple login failed: %s", type(exc).__name__)
                raise AppleLoginFailed() from None
        finally:
            with self._lock:
                self._starting -= 1
            if not keep:
                _rmdir(directory)

    def verify(self, user_id: int, flow_id: str, code: str) -> AppleSessionState:
        self._sweep()
        with self._lock:
            flow = self._flows.get(flow_id)
        if flow is None or flow.user_id != user_id:
            raise FlowExpired()
        with flow.lock:
            with self._lock:
                if self._flows.get(flow_id) is not flow:
                    raise FlowExpired()  # lost a race with another verify/cancel
            if flow.expires <= self._clock():
                self._drop(flow_id, flow)
                raise FlowExpired()
            flow.attempts += 1
            try:
                ok = bool(flow.service.validate_2fa_code(code))
            except Exception as exc:
                log.warning("2fa validation failed: %s", type(exc).__name__)
                self._drop(flow_id, flow)
                raise AppleLoginFailed() from None
            if not ok:
                if flow.attempts >= self._max_attempts:
                    self._drop(flow_id, flow)
                raise InvalidCode()
            try:
                state = _build_state(flow.service)
            except AppleLoginFailed:
                raise
            except Exception as exc:
                log.warning("apple login failed: %s", type(exc).__name__)
                raise AppleLoginFailed() from None
            finally:
                self._drop(flow_id, flow)
            return state

    def cancel(self, flow_id: str) -> None:
        self._sweep()
        with self._lock:
            flow = self._flows.pop(flow_id, None)
        if flow is not None:
            _rmdir(flow.directory)
            flow.service = None
