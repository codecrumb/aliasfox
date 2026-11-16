from fastapi.testclient import TestClient

from aliasfox.config import Settings
from aliasfox.main import create_app


def test_healthz_ok():
    client = TestClient(create_app(Settings(secret_key="x")))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
