from collections.abc import Generator
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import LOOPBACK_HOST, settings
from app.db.database import SessionLocal
from app.db.models import Branch
from app.main import app
from scripts import launch


@pytest.fixture
def shutdown_client() -> Generator[TestClient, None, None]:
    app.state.shutdown_token = "token-de-prueba"
    app.state.shutdown_callback = None
    with TestClient(app) as test_client:
        yield test_client
    app.state.shutdown_callback = None
    app.state.shutdown_token = ""


def test_shutdown_endpoint_invokes_registered_callback_after_response(
    shutdown_client: TestClient,
) -> None:
    calls: list[str] = []
    app.state.shutdown_callback = lambda: calls.append("shutdown")

    response = shutdown_client.post(
        "/system/shutdown", data={"shutdown_token": "token-de-prueba"}
    )

    assert response.status_code == 200
    assert "Sistema apagado" in response.text
    assert "Ya puedes cerrar esta ventana" in response.text
    assert calls == ["shutdown"]


def test_shutdown_get_does_not_stop_application(shutdown_client: TestClient) -> None:
    calls: list[str] = []
    app.state.shutdown_callback = lambda: calls.append("shutdown")

    response = shutdown_client.get("/system/shutdown")

    assert response.status_code == 405
    assert calls == []


def test_shutdown_requires_valid_token(shutdown_client: TestClient) -> None:
    calls: list[str] = []
    app.state.shutdown_callback = lambda: calls.append("shutdown")

    response = shutdown_client.post(
        "/system/shutdown", data={"shutdown_token": "incorrecto"}
    )

    assert response.status_code == 403
    assert "solicitud de apagado no era válida" in response.text
    assert calls == []


def test_shutdown_rejects_non_loopback_request() -> None:
    app.state.shutdown_token = "token-de-prueba"
    app.state.shutdown_callback = lambda: None
    with TestClient(app, client=("203.0.113.10", 50000)) as remote_client:
        response = remote_client.post(
            "/system/shutdown", data={"shutdown_token": "token-de-prueba"}
        )
    app.state.shutdown_callback = None
    app.state.shutdown_token = ""

    assert response.status_code == 403
    assert "Solicitud no permitida" in response.text


def test_shutdown_requires_launcher_callback(shutdown_client: TestClient) -> None:
    response = shutdown_client.post(
        "/system/shutdown", data={"shutdown_token": "token-de-prueba"}
    )

    assert response.status_code == 503
    assert "launcher local" in response.text


def test_shutdown_button_and_confirmation_are_visible_everywhere(
    shutdown_client: TestClient,
) -> None:
    home = shutdown_client.get("/")
    products = shutdown_client.get("/products")
    script = shutdown_client.get("/static/js/system.js")

    for response in (home, products):
        assert response.status_code == 200
        assert "Apagar sistema" in response.text
        assert 'method="post"' in response.text
        assert 'name="shutdown_token"' in response.text
    assert "¿Seguro que deseas apagar el sistema?" in script.text


def test_shutdown_preserves_persisted_data(shutdown_client: TestClient) -> None:
    with SessionLocal() as session:
        branches_before = session.scalar(select(func.count(Branch.id)))
    app.state.shutdown_callback = lambda: None

    response = shutdown_client.post(
        "/system/shutdown", data={"shutdown_token": "token-de-prueba"}
    )

    with SessionLocal() as session:
        branches_after = session.scalar(select(func.count(Branch.id)))
    assert response.status_code == 200
    assert branches_before == branches_after == 4


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


def test_health_check_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "is_application_running", lambda: False)
    monkeypatch.setattr(launch, "HEALTH_POLL_SECONDS", 0.001)

    assert launch.wait_for_application(timeout_seconds=0.003) is False


def test_existing_instance_is_reused_without_starting_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "ensure_runtime_directories", lambda: None)
    monkeypatch.setattr(launch, "configure_logging", lambda: None)
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
    assert opened_urls == ["http://127.0.0.1:8000/"]


def test_occupied_port_from_other_application_stops_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launch, "ensure_runtime_directories", lambda: None)
    monkeypatch.setattr(launch, "configure_logging", lambda: None)
    monkeypatch.setattr(launch, "is_application_running", lambda: False)
    monkeypatch.setattr(launch, "is_port_in_use", lambda: True)
    errors: list[str] = []
    monkeypatch.setattr(launch, "show_startup_error", errors.append)

    assert launch.run_launcher(open_browser=False) == 1
    assert errors == [
        "No pudimos iniciar el sistema porque el puerto local está ocupado. "
        "Cierra la otra aplicación e inténtalo nuevamente."
    ]


def test_browser_opens_only_after_health_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    browser_opened = threading.Event()
    captured_config: list[SimpleNamespace] = []

    class FakeServer:
        started = True
        should_exit = False

        def __init__(self, config: SimpleNamespace) -> None:
            captured_config.append(config)

        def run(self) -> None:
            assert browser_opened.wait(1)

    monkeypatch.setattr(launch, "ensure_runtime_directories", lambda: None)
    monkeypatch.setattr(launch, "configure_logging", lambda: None)
    monkeypatch.setattr(launch, "is_application_running", lambda: False)
    monkeypatch.setattr(launch, "is_port_in_use", lambda: False)
    monkeypatch.setattr(
        launch, "wait_for_application", lambda: order.append("health") or True
    )
    monkeypatch.setattr(
        launch.webbrowser,
        "open",
        lambda url: order.append(f"browser:{url}") or browser_opened.set() or True,
    )
    monkeypatch.setattr(
        launch.uvicorn,
        "Config",
        lambda app, **kwargs: SimpleNamespace(app=app, **kwargs),
    )
    monkeypatch.setattr(launch.uvicorn, "Server", FakeServer)

    assert launch.run_launcher(open_browser=True) == 0
    assert order == ["health", "browser:http://127.0.0.1:8000/"]
    assert captured_config[0].host == LOOPBACK_HOST
    assert captured_config[0].port == settings.port


def test_browser_does_not_open_when_health_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_stopped = threading.Event()
    errors: list[str] = []

    class FakeServer:
        started = True

        def __init__(self, _config: object) -> None:
            self._should_exit = False

        @property
        def should_exit(self) -> bool:
            return self._should_exit

        @should_exit.setter
        def should_exit(self, value: bool) -> None:
            self._should_exit = value
            if value:
                server_stopped.set()

        def run(self) -> None:
            assert server_stopped.wait(1)

    monkeypatch.setattr(launch, "ensure_runtime_directories", lambda: None)
    monkeypatch.setattr(launch, "configure_logging", lambda: None)
    monkeypatch.setattr(launch, "is_application_running", lambda: False)
    monkeypatch.setattr(launch, "is_port_in_use", lambda: False)
    monkeypatch.setattr(launch, "wait_for_application", lambda: False)
    monkeypatch.setattr(launch, "show_startup_error", errors.append)
    monkeypatch.setattr(
        launch.webbrowser,
        "open",
        lambda _url: (_ for _ in ()).throw(AssertionError("No debe abrir navegador")),
    )
    monkeypatch.setattr(launch.uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(launch.uvicorn, "Server", FakeServer)

    assert launch.run_launcher(open_browser=True) == 1
    assert errors == ["El servidor no respondió dentro del tiempo esperado."]


def test_keyboard_interrupt_is_a_clean_launcher_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptingServer:
        started = True
        should_exit = False

        def __init__(self, _config: object) -> None:
            pass

        def run(self) -> None:
            raise KeyboardInterrupt

    application = SimpleNamespace(
        state=SimpleNamespace(shutdown_callback=None, shutdown_token="")
    )
    monkeypatch.setattr(launch, "ensure_runtime_directories", lambda: None)
    monkeypatch.setattr(launch, "configure_logging", lambda: None)
    monkeypatch.setattr(launch, "is_application_running", lambda: False)
    monkeypatch.setattr(launch, "is_port_in_use", lambda: False)
    monkeypatch.setattr(launch, "wait_for_application", lambda: True)
    monkeypatch.setattr(launch, "load_application", lambda: application)
    monkeypatch.setattr(launch.uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(launch.uvicorn, "Server", InterruptingServer)

    assert launch.run_launcher(open_browser=False) == 0
    assert application.state.shutdown_callback is None
    assert application.state.shutdown_token == ""


def test_runtime_directories_are_created_without_deleting_data(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    database.write_bytes(b"datos-existentes")

    launch.ensure_runtime_directories(tmp_path)
    launch.ensure_runtime_directories(tmp_path)

    assert database.read_bytes() == b"datos-existentes"
    assert all((tmp_path / name).is_dir() for name in launch.RUNTIME_DIRECTORIES)


def test_runtime_permission_error_has_friendly_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[str] = []
    monkeypatch.setattr(
        launch,
        "ensure_runtime_directories",
        lambda: (_ for _ in ()).throw(PermissionError("detalle técnico")),
    )
    monkeypatch.setattr(launch, "show_startup_error", errors.append)

    assert launch.run_launcher(open_browser=False) == 1
    assert errors == [
        "No pudimos preparar los archivos locales. "
        "Revisa los permisos e inténtalo nuevamente."
    ]


def test_launcher_import_and_help_are_independent_of_cwd(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(launch.__file__).resolve()), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Inicia el sistema local de ventas" in result.stdout


def test_host_and_port_are_centralized_and_local_only() -> None:
    assert settings.host == LOOPBACK_HOST == "127.0.0.1"
    assert launch.application_url() == f"http://{LOOPBACK_HOST}:{settings.port}/"


def test_startup_error_is_printed_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(launch.os, "name", "posix")

    launch.show_startup_error("Error de prueba")

    assert capsys.readouterr().out == "Error de prueba\n"
