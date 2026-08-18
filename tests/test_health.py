from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["app"] == "ok"
        assert payload["trading_mode"] in {"OBSERVE", "PAPER"}


def test_gemini_usage_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/system/gemini-usage")
        assert response.status_code == 200
        assert "configured_keys" in response.json()
