"""Password + 2FA spike using pyicloud. Run by the owner in a real terminal.

    .venv/bin/python scripts/spike_pyicloud.py

Prompts for Apple ID (input) and password (getpass, never echoed). Nothing is
stored or logged; the cookie directory is a temp dir deleted in a finally.
Prints ONLY booleans, counts, cookie NAMES and the premiummailsettings host.

pyicloud API notes (verified by reading pyicloud 2026.11.2 source):
- Class is pyicloud.PyiCloudService(apple_id, password=None,
  cookie_directory=None, verify=True, client_id=None, with_family=True,
  china_mainland=None, ..., authenticate=True, pause_2fa=False).
  cookie_directory=None falls back to a default home-dir path, so we always
  pass a temp dir.
- Constructing with authenticate=True does NOT raise on 2FA; it sets
  requires_2fa / requires_2sa. Then: request_2fa_code() triggers delivery,
  two_factor_delivery_method is "trusted_device" | "sms" | "security_key" |
  "unknown", and validate_2fa_code(code) verifies and trusts the session.
  Security-key 2FA needs a hardware key (confirm_security_key), not handled here.
- HideMyEmailService has NO list(); it supports len() and iteration, so a
  count is len(list(api.hidemyemail)).
- Cookies live in api.session.cookies (a cookiejar subclass); iterate cookies
  and read .name. Session data (session_token etc.) is in api.session.data.
- Web service URLs: api.webservices["premiummailsettings"]["url"].
- Exceptions can embed server text, so only class names are printed.
"""

import getpass
import shutil
import sys
import tempfile
from urllib.parse import urlparse


def cookie_names(jar) -> list[str]:
    return sorted({str(c.name) for c in jar})


def url_host(url) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    return urlparse(url).hostname


def premiummail_host(webservices) -> str | None:
    if not isinstance(webservices, dict):
        return None
    entry = webservices.get("premiummailsettings")
    return url_host(entry.get("url")) if isinstance(entry, dict) else None


def two_factor_kind(api) -> str:
    kind = getattr(api, "two_factor_delivery_method", "unknown")
    return kind if kind in ("trusted_device", "sms", "security_key") else "unknown"


def format_report(report: dict) -> list[str]:
    return [f"{k}: {v}" for k, v in report.items()]


def run() -> int:
    from pyicloud import PyiCloudService

    apple_id = input("Apple ID: ").strip()
    password = getpass.getpass("Password (hidden): ")
    cookie_dir = tempfile.mkdtemp(prefix="aliasfox-spike-")
    report: dict = {}
    code = 1
    try:
        api = PyiCloudService(apple_id, password, cookie_directory=cookie_dir)
        del password
        needs_2fa = bool(api.requires_2fa)
        report["2fa required"] = needs_2fa
        report["legacy 2sa required"] = bool(api.requires_2sa)
        if needs_2fa:
            try:
                api.request_2fa_code()
            except Exception as exc:
                print(f"request_2fa_code failed: {type(exc).__name__}")
            kind = two_factor_kind(api)
            report["2fa kind"] = kind
            if kind == "security_key":
                print("security key 2FA cannot be completed in this script")
                for line in format_report(report):
                    print(line)
                return 1
            entered = input("2FA code: ").strip()
            report["2fa code accepted"] = bool(api.validate_2fa_code(entered))
        report["login succeeded"] = not (api.requires_2fa or api.requires_2sa)
        report["session cookie names"] = cookie_names(api.session.cookies)
        report["session data key names"] = sorted(str(k) for k in api.session.data)
        host = premiummail_host(api.webservices)
        report["premiummailsettings available"] = host is not None
        report["premiummailsettings host"] = host
        try:
            report["hidemyemail alias count"] = len(list(api.hidemyemail))
        except Exception as exc:
            report["hidemyemail list failed"] = type(exc).__name__
        code = 0 if report["login succeeded"] else 1
    except Exception as exc:
        report["login succeeded"] = False
        report["error"] = type(exc).__name__
    finally:
        shutil.rmtree(cookie_dir, ignore_errors=True)
    for line in format_report(report):
        print(line)
    return code


def main() -> int:
    if not sys.stdin.isatty():
        print("refusing to run: stdin is not a TTY")
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main())
