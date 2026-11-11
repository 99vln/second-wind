from fastapi.testclient import TestClient

from app.main import app
from app.services.status import get_status

client = TestClient(app)


def test_status_service_reports_ok():
    status = get_status()
    assert status.ok is True
    assert status.app == "Second Wind"


def test_api_health_returns_json():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"app": "Second Wind", "ok": True}


def test_manifest_is_served_for_pwa_install():
    response = client.get("/static/manifest.webmanifest")
    assert response.status_code == 200
    assert response.json()["display"] == "standalone"


def test_service_worker_is_served_from_root():
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
