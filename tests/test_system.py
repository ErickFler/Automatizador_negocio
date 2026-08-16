from collections.abc import Generator
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from scripts import launch


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
    if hasattr(app.state, "shutdown_callback"):
        del app.state.shutdown_callback


def test_shutdown_endpoint_invokes_registered_callback(client: TestClient) -> None:
    calls: list[str] = []
    app.state.shutdown_callback = lambda: calls.append("shutdown")

    response = client.post("/system/shutdown")

    assert response.status_code == 200
    assert "Apagando sistema" in response.text
    assert "apagando correctamente" in response.text
    assert calls == ["shutdown"]


def test_shutdown_requires_launcher_callback(client: TestClient) -> None:
    response = client.post("/system/shutdown")

    assert response.status_code == 503
    assert "launcher local" in response.text


def test_health_check_recognizes_this_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps(
        {"status": "ok", "application": "Sistema de Concentrado de Ventas"}
    ).encode()

    class FakeResponse:
        status = 200

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return payload

    monkeypatch.setattr(launch, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    assert launch.is_application_running() is True


def test_existing_instance_is_reused_without_starting_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "is_application_running", lambda: True)
    monkeypatch.setattr(
        launch,
        "is_port_in_use",
        lambda: (_ for _ in ()).throw(AssertionError("No debe revisar el puerto")),
    )
    opened_urls: list[str] = []
    monkeypatch.setattr(launch.webbrowser, "open", opened_urls.append)

    exit_code = launch.run_launcher(open_browser=True)

    assert exit_code == 0
    assert opened_urls == ["http://127.0.0.1:8000"]


def test_occupied_port_from_other_application_stops_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "is_application_running", lambda: False)
    monkeypatch.setattr(launch, "is_port_in_use", lambda: True)
    errors: list[str] = []
    monkeypatch.setattr(launch, "show_startup_error", errors.append)

    assert launch.run_launcher(open_browser=False) == 1
    assert errors == [
        "No se puede iniciar: el puerto 8000 está ocupado por otra aplicación."
    ]


def test_startup_error_is_printed_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(launch.os, "name", "posix")

    launch.show_startup_error("Error de prueba")

    assert capsys.readouterr().out == "Error de prueba\n"
