import argparse
import json
import logging
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

# Permite ejecutar este archivo desde cualquier directorio de trabajo.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn

from app.core.config import DATA_ROOT, LOOPBACK_HOST, settings
from app.core.logging import configure_logging

STARTUP_TIMEOUT_SECONDS = 20.0
HEALTH_POLL_SECONDS = 0.2
RUNTIME_DIRECTORIES = ("uploads", "cost_uploads", "logs")
logger = logging.getLogger(__name__)


def ensure_runtime_directories(data_root: Path = DATA_ROOT) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    for directory_name in RUNTIME_DIRECTORIES:
        (data_root / directory_name).mkdir(parents=True, exist_ok=True)


def load_application():
    from app.main import app

    return app


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
    return f"http://{LOOPBACK_HOST}:{settings.port}/"


def is_application_running() -> bool:
    try:
        with urlopen(f"{application_url()}health", timeout=0.7) as response:
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
        with socket.create_connection((LOOPBACK_HOST, settings.port), timeout=0.5):
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


def run_launcher(*, open_browser: bool = True) -> int:
    try:
        ensure_runtime_directories()
        configure_logging()
    except OSError:
        show_startup_error(
            "No pudimos preparar los archivos locales. Revisa los permisos e inténtalo nuevamente."
        )
        return 1

    logger.info("Launcher iniciado")
    if is_application_running():
        logger.info("Se reutilizara una instancia existente")
        if open_browser:
            webbrowser.open(application_url())
            logger.info("Navegador abierto")
        return 0
    if is_port_in_use():
        message = (
            "No pudimos iniciar el sistema porque el puerto local está ocupado. "
            "Cierra la otra aplicación e inténtalo nuevamente."
        )
        logger.error("Puerto local ocupado: %s", settings.port)
        show_startup_error(message)
        return 1

    application = load_application()

    config = uvicorn.Config(
        application,
        host=LOOPBACK_HOST,
        port=settings.port,
        log_level=settings.log_level.lower(),
        # The Windows executable is built in ``windowed`` mode, where
        # sys.stderr is None. Uvicorn's default dictConfig formatter probes
        # that stream with isatty(), so keep the file-based app logging above
        # and let Uvicorn use the existing logging configuration.
        log_config=None,
    )
    server = uvicorn.Server(config)
    application.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    application.state.shutdown_token = secrets.token_urlsafe(32)
    startup_failed = threading.Event()

    def open_when_ready() -> None:
        if wait_for_application():
            logger.info("Servidor disponible en %s", application_url())
            if open_browser:
                webbrowser.open(application_url())
                logger.info("Navegador abierto")
        else:
            startup_failed.set()
            message = "El servidor no respondió dentro del tiempo esperado."
            logger.error(message)
            show_startup_error(message)
            server.should_exit = True

    startup_thread = threading.Thread(target=open_when_ready, daemon=True)
    startup_thread.start()
    interrupted = False
    try:
        server.run()
    except KeyboardInterrupt:
        interrupted = True
        logger.info("Cierre solicitado desde el launcher")
    except (OSError, RuntimeError):
        logger.exception("No fue posible iniciar el servidor local")
        show_startup_error(
            "No pudimos iniciar el sistema. Revisa el archivo de log e inténtalo nuevamente."
        )
        return 1
    finally:
        server.should_exit = True
        startup_thread.join(timeout=1.0)
        application.state.shutdown_callback = None
        application.state.shutdown_token = ""
        logger.info("Launcher detenido")
    return 0 if (server.started or interrupted) and not startup_failed.is_set() else 1


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
    multiprocessing.freeze_support()
    raise SystemExit(main())
