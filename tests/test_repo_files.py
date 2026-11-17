import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MULTI_VARS = [
    "ALIASFOX_MODE", "NEON_AUTH_BASE_URL", "NEON_AUTH_JWKS_URL", "NEON_AUTH_ISSUER",
    "SIGNUPS_ENABLED", "SIGNUP_ALLOWLIST", "PUBLIC_URL", "CONTACT_EMAIL",
    "TRUSTED_PROXY", "DATABASE_URL",
]


def test_license_is_agpl3():
    text = (ROOT / "LICENSE").read_text()
    assert text.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
    assert "Version 3, 19 November 2007" in text[:200]
    assert "How to Apply These Terms" in text
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "AGPL-3.0-or-later"


def test_security_md_has_reporting_route_and_no_promises():
    text = (ROOT / "SECURITY.md").read_text()
    assert "private vulnerability reporting" in text.lower()
    assert "CONTACT_EMAIL" in text
    assert "no guarantee" in text.lower()
    assert "@" not in text


def test_readme_mentions_unofficial_and_no_warranty_and_multi_mode_vars():
    text = (ROOT / "README.md").read_text()
    low = text.lower()
    assert "not affiliated" in low
    assert "no warranty" in low
    for v in MULTI_VARS:
        assert v in text, v
    assert "SECURITY.md" in text and "LICENSE" in text
    assert "plan 3" not in low


def test_env_example_lists_every_new_setting():
    cfg = (ROOT / "src/aliasfox/config.py").read_text()
    names = set(re.findall(r'(?:_env|AliasChoices)\(\s*"([A-Z_]+)"', cfg))
    assert len(names) >= 18
    example = (ROOT / ".env.example").read_text()
    for n in names:
        assert re.search(rf"^#?\s*{n}=", example, re.M), n
