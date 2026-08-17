import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app, unexpected_error_page


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
    assert "Catálogo de productos" in response.text


def test_unknown_route_has_friendly_error_page() -> None:
    with TestClient(app) as client:
        response = client.get("/esta-ruta-no-existe")

    assert response.status_code == 404
    assert "Página no encontrada" in response.text
    assert "Volver al inicio" in response.text
    assert '"detail":"Not Found"' not in response.text


def test_invalid_form_data_has_friendly_validation_page() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/imports",
            data={"sales_date": "2026-08-16", "branch_id": "no-es-una-sucursal"},
        )

    assert response.status_code == 422
    assert "Hay datos que debemos revisar" in response.text
    assert "Verifique las fechas" in response.text


def test_unexpected_error_has_friendly_page_and_is_logged(monkeypatch) -> None:
    logged: list[tuple[str, str]] = []
    logger = SimpleNamespace(
        exception=lambda message, path: logged.append((message, path))
    )
    monkeypatch.setattr("app.main.get_logger", lambda _name: logger)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/falla-controlada",
            "raw_path": b"/falla-controlada",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
            "app": app,
        }
    )

    response = asyncio.run(unexpected_error_page(request, RuntimeError("secreto")))

    assert response.status_code == 500
    assert b"Ocurri\xc3\xb3 un problema inesperado" in response.body
    assert b"secreto" not in response.body
    assert logged == [("Error inesperado al atender %s", "/falla-controlada")]
