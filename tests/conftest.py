import pytest


@pytest.fixture(autouse=True)
def _hermetic_web_dist(tmp_path_factory, monkeypatch):
    """No test may read the real src/aliasfox/static build; tests that want a
    dist pass one explicitly or override WEB_DIST_DIR."""
    monkeypatch.setenv("WEB_DIST_DIR", str(tmp_path_factory.mktemp("nodist") / "absent"))
