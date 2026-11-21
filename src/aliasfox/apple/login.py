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
import os
import secrets
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from aliasfox.apple.connect import AppleUnreachable
from aliasfox.apple.hme import UntrustedAppleUrl, check_webservices_url
from aliasfox.sessions import AppleSessionState

log = logging.getLogger(__name__)


class AppleLoginFailed(Exception):
    """Apple login failed. Fixed message: never carries Apple/pyicloud text."""

    def __init__(self) -> None:
        super().__init__("apple login failed")


class AppleAccountLocked(AppleLoginFailed):
    """Apple reports the account locked (too many attempts or a security hold)."""


class AppleLoginUnsupported(AppleLoginFailed):
    """The account needs a sign-in method AliasFox can't do (security key, legacy 2SA)."""


class InvalidCode(Exception):
    def __init__(self) -> None:
        super().__init__("invalid verification code")


class FlowExpired(Exception):
    def __init__(self) -> None:
        super().__init__("login flow expired or unknown")


class TooManyFlows(Exception):
    def __init__(self) -> None:
        super().__init__("too many pending login flows")


class TooManyResends(Exception):
    def __init__(self) -> None:
        super().__init__("too many code resends for this login")


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
    resends: int = 0
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


LOGIN_DIR_PREFIX = "aliasfox-login-"


def cleanup_stale_login_dirs(max_age_seconds: float) -> int:
    """Remove leftover `aliasfox-login-*` dirs in the temp dir older than the
    given age (e.g. from a crash). Touches nothing else. Returns the count."""
    root = tempfile.gettempdir()
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(LOGIN_DIR_PREFIX):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                continue
            if os.stat(path).st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def _build_state(api) -> AppleSessionState:
    cookies = {str(c.name): str(c.value) for c in api.session.cookies}
    ws = api.webservices
    entry = ws.get("premiummailsettings") if isinstance(ws, dict) else None
    url = entry.get("url") if isinstance(entry, dict) else None
    if not isinstance(url, str) or not url:
        raise AppleLoginFailed()
    check_webservices_url(url)
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
        max_resends: int = 3,
    ) -> None:
        self._factory = service_factory
        self._clock = clock
        self._ttl = ttl_seconds
        self._max_pending = max_pending
        self._max_attempts = max_code_attempts
        self._max_resends = max_resends
        self._lock = threading.Lock()
        self._flows: dict[str, _Flow] = {}
        self._starting = 0  # slots reserved by in-progress start() calls

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def sweep(self) -> None:
        self._sweep()

    def shutdown(self) -> None:
        """Destroy every pending flow (app shutdown), removing all temp dirs."""
        with self._lock:
            flows = list(self._flows.values())
            self._flows.clear()
        for flow in flows:
            self._destroy(flow)

    def _sweep(self) -> None:
        """Remove expired flows. A flow whose lock is held is mid-verify: leave
        it (verify removes its own directory) so pyicloud never writes tokens
        into a directory we already deleted."""
        now = self._clock()
        with self._lock:
            for key, f in list(self._flows.items()):
                if f.expires > now or not f.lock.acquire(blocking=False):
                    continue
                try:
                    del self._flows[key]
                    _rmdir(f.directory)
                    f.service = None
                finally:
                    f.lock.release()

    def _destroy(self, flow: _Flow) -> None:
        """Remove an already-unregistered flow once any in-progress verify ends."""
        with flow.lock:
            _rmdir(flow.directory)
            flow.service = None

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
        self.cancel_user(user_id)  # at most one pending flow per user
        with self._lock:
            if len(self._flows) + self._starting >= self._max_pending:
                raise TooManyFlows()
            self._starting += 1
        directory: str | None = None
        keep = False
        try:
            directory = tempfile.mkdtemp(prefix=LOGIN_DIR_PREFIX)  # mode 0700
            try:
                api = self._factory(
                    apple_id, password, cookie_directory=directory, china_mainland=china
                )
            except Exception as exc:
                log.warning("apple login failed: %s", type(exc).__name__)
                # requests' connection and timeout errors are OSErrors; pyicloud's own errors are not.
                if isinstance(exc, OSError):
                    raise AppleUnreachable() from None
                if type(exc).__name__ == "PyiCloudAccountLockedException":
                    raise AppleAccountLocked() from None
                raise AppleLoginFailed() from None
            finally:
                password = None  # drop our reference; pyicloud holds its own copy
            try:
                if api.requires_2fa:
                    # NB: pyicloud also reports requires_2sa for HSA2 accounts, so
                    # 2FA must be decided first.
                    if getattr(api, "two_factor_delivery_method", "") == "security_key":
                        raise AppleLoginUnsupported()  # hardware key unsupported
                elif api.requires_2sa:
                    raise AppleLoginUnsupported()  # legacy 2SA only: unsupported
                else:
                    return LoginResult("ok", None, _build_state(api))
                flow_id = secrets.token_urlsafe(32)
                flow = _Flow(user_id, api, directory, self._clock() + self._ttl)
                # The password is already accepted: answer now and request the code in the
                # background. The flow stays locked until that request ends, so verify, resend,
                # sweep and cancel all wait for it instead of racing it.
                flow.lock.acquire()
                try:
                    threading.Thread(
                        target=self._request_code, args=(flow,), daemon=True, name="aliasfox-2fa-request"
                    ).start()
                except BaseException:
                    flow.lock.release()
                    raise
                with self._lock:
                    self._flows[flow_id] = flow
                keep = True
                return LoginResult("needs_2fa", flow_id, None)
            except (AppleLoginFailed, UntrustedAppleUrl):
                raise
            except Exception as exc:
                log.warning("apple login failed: %s", type(exc).__name__)
                raise AppleLoginFailed() from None
        finally:
            with self._lock:
                self._starting -= 1
            if not keep and directory is not None:
                _rmdir(directory)

    @staticmethod
    def _request_code(flow: _Flow) -> None:
        """Ask Apple to send the code. Runs with flow.lock held (taken by start()) and releases it."""
        try:
            flow.service.request_2fa_code()
        except Exception as exc:  # Apple may have pushed the code anyway
            log.info("request_2fa_code failed: %s", type(exc).__name__)
        finally:
            flow.lock.release()

    def resend(self, user_id: int, flow_id: str) -> None:
        """Ask Apple for a fresh code on a pending flow (capped per flow)."""
        self._sweep()
        with self._lock:
            flow = self._flows.get(flow_id)
        if flow is None or flow.user_id != user_id:
            raise FlowExpired()
        with flow.lock:
            with self._lock:
                if self._flows.get(flow_id) is not flow:
                    raise FlowExpired()
            if flow.expires <= self._clock():
                self._drop(flow_id, flow)
                raise FlowExpired()
            if flow.resends >= self._max_resends:
                raise TooManyResends()
            flow.resends += 1
            try:
                flow.service.request_2fa_code()
            except Exception as exc:  # Apple may have pushed it anyway
                log.info("request_2fa_code failed: %s", type(exc).__name__)

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
                # No automatic new code here: a second prompt on the user's devices after a
                # wrong or flaky entry is confusing. pyicloud clears its trusted-device bridge
                # state after every validate call, so the user asks for a fresh code with resend().
                raise InvalidCode()
            self._trust(flow.service)
            try:
                state = _build_state(flow.service)
            except (AppleLoginFailed, UntrustedAppleUrl):
                raise
            except Exception as exc:
                log.warning("apple login failed: %s", type(exc).__name__)
                raise AppleLoginFailed() from None
            finally:
                self._drop(flow_id, flow)
            return state

    @staticmethod
    def _trust(service: Any) -> None:
        """Ask Apple to trust this session. The code was already accepted, so a
        failure here must not fail the login."""
        try:
            if not getattr(service, "is_trusted_session", True):
                service.trust_session()
        except Exception as exc:
            log.warning("trust_session failed: %s", type(exc).__name__)

    def cancel(self, flow_id: str) -> None:
        self._sweep()
        with self._lock:
            flow = self._flows.pop(flow_id, None)
        if flow is not None:
            self._destroy(flow)

    def cancel_user(self, user_id: int) -> None:
        self._sweep()
        with self._lock:
            keys = [k for k, f in self._flows.items() if f.user_id == user_id]
            flows = [self._flows.pop(k) for k in keys]
        for flow in flows:
            self._destroy(flow)
