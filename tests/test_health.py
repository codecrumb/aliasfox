from fastapi.testclient import TestClient

from aliasfox.config import Settings
from aliasfox.main import create_app


def test_healthz_ok(tmp_path):
    client = TestClient(create_app(Settings(secret_key="x" * 32, data_dir=tmp_path)))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert (tmp_path / "aliasfox.db").exists()
