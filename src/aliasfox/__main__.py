"""`python -m aliasfox`: run the server, or one of the helper subcommands."""

import argparse
import getpass
import re
import secrets
import sys

import uvicorn

from aliasfox.config import ConfigError, get_settings
from aliasfox.crypto import hash_password
from aliasfox.main import create_app

MIN_PASSWORD_LENGTH = 12


def password_problem(pw: str) -> str | None:
    """Owner's password policy: 12+ characters, at least one digit and one symbol."""
    if len(pw) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    if not re.search(r"\d", pw):
        return "password must contain a digit"
    if not re.search(r"[^\w\s]|_", pw):
        return "password must contain a symbol"
    return None


def _hash_password_cmd() -> int:
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        print("error: passwords do not match", file=sys.stderr)
        return 1
    problem = password_problem(first)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    print(hash_password(first))
    return 0


def _serve() -> int:
    settings = get_settings()
    app = create_app(settings)  # runs validate_startup on the real bind address
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,  # rate limiters and pending login flows are in memory
        proxy_headers=False,  # X-Forwarded-For is parsed by us, only if TRUSTED_PROXY
        log_level="info",
        access_log=False,  # never log request lines, headers, bodies or cookies
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aliasfox")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the server (default)")
    sub.add_parser("hash-password", help="print an Argon2id hash for ALIASFOX_PASSWORD_HASH")
    sub.add_parser("gen-secret", help="print a random value for ALIASFOX_SECRET_KEY")
    args = parser.parse_args(argv)
    if args.command == "hash-password":
        return _hash_password_cmd()
    if args.command == "gen-secret":
        print(secrets.token_urlsafe(48))
        return 0
    return _serve()


def _cli() -> None:
    try:
        code = main()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    _cli()
