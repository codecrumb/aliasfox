"""Read-only live spike for the iCloud cookie path.

Usage: python scripts/spike.py [ENV_FILE] [--no-dsid]
ENV_FILE defaults to .env.local and must define ICLOUD_COOKIE.

Prints ONLY non-personal facts: counts, field NAMES, origin/isActive tallies,
timings, booleans, exception class names. Never addresses, labels, notes,
cookie names/values or tokens. Only calls validate and the list endpoint.
"""

import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aliasfox.apple.connect import connect_with_cookie, parse_cookie_header  # noqa: E402
from aliasfox.apple.hme import HmeClient, HmeError  # noqa: E402


def load_env_value(path: str | Path, key: str) -> str | None:
    """Return the value of KEY from a simple KEY=VALUE env file, or None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value
    return None


def describe_error(exc: BaseException) -> str:
    """Exception class name, plus the numeric code for HmeError. No messages."""
    name = type(exc).__name__
    if isinstance(exc, HmeError) and isinstance(exc.code, int):
        return f"{name} code={exc.code}"
    return name


def summarize(raw_aliases: list[dict], top_level_keys: list[str]) -> dict:
    """Build a value-free summary from raw alias dicts."""
    fields: set[str] = set()
    origins: Counter = Counter()
    active: Counter = Counter()
    for d in raw_aliases:
        fields.update(str(k) for k in d)
        origins[str(d.get("origin"))] += 1
        active[str(d.get("isActive"))] += 1
    return {
        "alias_count": len(raw_aliases),
        "field_names": sorted(fields),
        "origin_counts": dict(sorted(origins.items())),
        "is_active_counts": dict(sorted(active.items())),
        "list_result_keys": sorted(top_level_keys),
    }


def format_summary(s: dict) -> list[str]:
    return [
        f"alias count: {s['alias_count']}",
        f"alias field names: {s['field_names']}",
        f"origin counts: {s['origin_counts']}",
        f"isActive counts: {s['is_active_counts']}",
        f"list result top-level keys: {s['list_result_keys']}",
    ]


def parse_args(argv: list[str]) -> tuple[str, bool]:
    no_dsid = "--no-dsid" in argv
    rest = [a for a in argv if not a.startswith("--")]
    return (rest[0] if rest else ".env.local"), no_dsid


def run(env_path: str, no_dsid: bool) -> int:
    cookie = load_env_value(env_path, "ICLOUD_COOKIE")
    if not cookie:
        print("ICLOUD_COOKIE not found in env file")
        return 1
    try:
        original = parse_cookie_header(cookie)
        t0 = time.perf_counter()
        state = connect_with_cookie(cookie)
        t_validate = time.perf_counter() - t0
    except Exception as exc:
        print(f"validate succeeded: False ({describe_error(exc)})")
        return 1
    print("validate succeeded: True")
    print(f"validate time: {t_validate:.2f}s")

    client = HmeClient(state)
    try:
        t0 = time.perf_counter()
        aliases = client.list()
        t_list = time.perf_counter() - t0
        raw_result = client.list_raw()
        for line in format_summary(
            summarize([a.raw for a in aliases], list(raw_result))
        ):
            print(line)
        print(f"list time: {t_list:.2f}s")
        print(f"set-cookie rotation changed cookies: {state.cookies != original}")
        if no_dsid:
            state.dsid = None
            try:
                client.list_raw()
                print("list without dsid works: True")
            except Exception as exc:
                print(f"list without dsid works: False ({describe_error(exc)})")
        return 0
    except Exception as exc:
        print(f"list failed: {describe_error(exc)}")
        return 1
    finally:
        client.close()


def main() -> int:
    env_path, no_dsid = parse_args(sys.argv[1:])
    return run(env_path, no_dsid)


if __name__ == "__main__":
    sys.exit(main())
