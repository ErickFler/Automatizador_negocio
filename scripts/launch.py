import argparse
import json
import os
import socket
import threading
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

import uvicorn

from app.core.config import settings
from app.main import app

STARTUP_TIMEOUT_SECONDS = 20.0
HEALTH_POLL_SECONDS = 0.2


def show_startup_error(message: str) -> None:
    """Show launcher failures even when the Windows build has no console."""
    print(message)
    if os.name != "nt":
        return

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None,
            message,
            "Concentrado de Ventas",
            0x10,
        )
    except (AttributeError, OSError):
        # Logging still captures the failure if Windows cannot show the dialog.
        pass


def application_url() -> str:
    return f"http://{settings.host}:{settings.port}"


def is_application_running() -> bool:
    try:
        with urlopen(f"{application_url()}/health", timeout=0.7) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        response.status == 200
        and payload.get("status") == "ok"
        and payload.get("application") == settings.app_name
    )


def is_port_in_use() -> bool:
    try:
        with socket.create_connection((settings.host, settings.port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_for_application(timeout_seconds: float = STARTUP_TIMEOUT_SECONDS) -> bool:
    deadline = threading.Event()
    attempts = max(1, int(timeout_seconds / HEALTH_POLL_SECONDS))
    for _ in range(attempts):
        if is_application_running():
            return True
        deadline.wait(HEALTH_POLL_SECONDS)
    return False


def schedule_shutdown(server: uvicorn.Server) -> None:
    timer = threading.Timer(0.4, lambda: setattr(server, "should_exit", True))
    timer.daemon = True
    timer.start()


def run_launcher(*, open_browser: bool = True) -> int:
    if is_application_running():
        if open_browser:
            webbrowser.open(application_url())
        return 0
    if is_port_in_use():
        show_startup_error(
            f"No se puede iniciar: el puerto {settings.port} está ocupado por otra aplicación."
        )
        return 1

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    app.state.shutdown_callback = lambda: schedule_shutdown(server)

    def open_when_ready() -> None:
        if wait_for_application():
            if open_browser:
                webbrowser.open(application_url())
        else:
            show_startup_error("El servidor no respondió dentro del tiempo esperado.")
            server.should_exit = True

    startup_thread = threading.Thread(target=open_when_ready, daemon=True)
    startup_thread.start()
    try:
        server.run()
    finally:
        if hasattr(app.state, "shutdown_callback"):
            del app.state.shutdown_callback
    return 0 if server.started else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia el sistema local de ventas.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Inicia sin abrir el navegador; útil para verificaciones.",
    )
    arguments = parser.parse_args()
    return run_launcher(open_browser=not arguments.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
