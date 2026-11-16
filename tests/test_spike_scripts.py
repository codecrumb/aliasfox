import importlib.util
import subprocess
import sys
from http.cookiejar import Cookie
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


spike = load("spike")
spike_py = load("spike_pyicloud")


def test_load_env_value(tmp_path):
    f = tmp_path / "env"
    f.write_text("# c\nA=1\nexport ICLOUD_COOKIE='x=y; z=w=='\nB=2\n")
    assert spike.load_env_value(f, "ICLOUD_COOKIE") == "x=y; z=w=="
    assert spike.load_env_value(f, "A") == "1"
    assert spike.load_env_value(f, "MISSING") is None
    assert spike.load_env_value(tmp_path / "nope", "A") is None


def test_summarize_has_no_values():
    raw = [
        {"hme": "a@icloud.com", "label": "SECRETLABEL", "origin": "ON_DEMAND", "isActive": True},
        {"hme": "b@icloud.com", "note": "SECRETNOTE", "origin": "SAFARI", "isActive": False},
    ]
    s = spike.summarize(raw, ["hmeEmails", "next"])
    out = "\n".join(spike.format_summary(s))
    assert s["alias_count"] == 2
    assert s["field_names"] == ["hme", "isActive", "label", "note", "origin"]
    assert s["origin_counts"] == {"ON_DEMAND": 1, "SAFARI": 1}
    assert s["is_active_counts"] == {"False": 1, "True": 1}
    assert s["list_result_keys"] == ["hmeEmails", "next"]
    for secret in ("a@icloud.com", "SECRETLABEL", "SECRETNOTE"):
        assert secret not in out


def test_describe_error_class_and_code_only():
    from aliasfox.apple.hme import HmeError

    assert spike.describe_error(HmeError(503, "SECRETMSG")) == "HmeError code=503"
    assert spike.describe_error(HmeError(None, "SECRETMSG")) == "HmeError"
    assert spike.describe_error(ValueError("SECRETMSG")) == "ValueError"


def test_parse_args():
    assert spike.parse_args([]) == (".env.local", False)
    assert spike.parse_args(["my.env", "--no-dsid"]) == ("my.env", True)


def test_pyicloud_helpers():
    def ck(name):
        return Cookie(0, name, "SECRETVAL", None, False, "d", False, False, "/", False,
                      False, None, False, None, None, {})

    assert spike_py.cookie_names([ck("b"), ck("a")]) == ["a", "b"]
    assert spike_py.url_host("https://p1-maildomainws.icloud.com:443/x") == "p1-maildomainws.icloud.com"
    assert spike_py.url_host(None) is None
    ws = {"premiummailsettings": {"url": "https://h.icloud.com", "status": "active"}}
    assert spike_py.premiummail_host(ws) == "h.icloud.com"
    assert spike_py.premiummail_host({}) is None


def test_pyicloud_refuses_without_tty():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "spike_pyicloud.py")],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    assert r.returncode == 2


def test_spike_results_separates_write_questions():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "spike-results.md").read_text()
    header = "## Requires a write test — needs explicit owner consent — not part of this spike"
    readonly, _, writes = text.partition(header)
    assert writes
    for q in ("Generate without reserve consumes quota", "`update_metadata` with `note` omitted"):
        assert q in writes and q not in readonly
    assert "$TMPDIR/aliasfox-spike-*" in readonly
