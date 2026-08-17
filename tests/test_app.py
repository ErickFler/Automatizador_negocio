from fastapi.testclient import TestClient

from app.main import app


def test_home_page() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Control y análisis de ventas" in response.text
    assert "Está viendo" in response.text


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application": "Sistema de Concentrado de Ventas",
    }


def test_products_page_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/products")

    assert response.status_code == 200
    assert "Productos maestros" in response.text
