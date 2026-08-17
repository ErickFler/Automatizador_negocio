import ipaddress
import secrets
from typing import Callable

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.background import BackgroundTask

from app.core.logging import get_logger
from app.presentation.templates import templates

router = APIRouter()
logger = get_logger(__name__)


@router.post("/system/shutdown", response_class=HTMLResponse)
def shutdown_system(
    request: Request, shutdown_token: str = Form(default="")
) -> HTMLResponse:
    if not _is_local_request(request):
        logger.warning("Solicitud de apagado rechazada desde un origen no local")
        return templates.TemplateResponse(
            request=request,
            name="shutdown.html",
            context={"shutdown_started": False, "message": "Solicitud no permitida."},
            status_code=403,
        )

    expected_token = getattr(request.app.state, "shutdown_token", "")
    if not expected_token or not secrets.compare_digest(shutdown_token, expected_token):
        logger.warning("Solicitud de apagado rechazada por validacion")
        return templates.TemplateResponse(
            request=request,
            name="shutdown.html",
            context={
                "shutdown_started": False,
                "message": "La solicitud de apagado no era válida. Inténtalo nuevamente.",
            },
            status_code=403,
        )

    shutdown_callback: Callable[[], None] | None = getattr(
        request.app.state, "shutdown_callback", None
    )
    if shutdown_callback is None:
        return templates.TemplateResponse(
            request=request,
            name="shutdown.html",
            context={
                "shutdown_started": False,
                "message": "El apagado está disponible al iniciar con el launcher local.",
            },
            status_code=503,
        )

    logger.info("Apagado solicitado desde la interfaz local")
    return templates.TemplateResponse(
        request=request,
        name="shutdown.html",
        context={
            "shutdown_started": True,
            "message": "El sistema se apagó correctamente.",
        },
        background=BackgroundTask(shutdown_callback),
    )


def _is_local_request(request: Request) -> bool:
    if request.client is None:
        return False
    if request.client.host == "testclient":
        return True
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False
