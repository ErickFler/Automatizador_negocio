from fastapi.testclient import TestClient

from app.main import app


def test_home_page() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Sistema de Concentrado de Ventas" in response.text
    assert "Aplicación funcionando correctamente" in response.text


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application": "Sistema de Concentrado de Ventas",
    }

