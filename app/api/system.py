import ipaddress
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.pages import templates

router = APIRouter()


@router.post("/system/shutdown", response_class=HTMLResponse)
def shutdown_system(request: Request) -> HTMLResponse:
    if not _is_local_request(request):
        return templates.TemplateResponse(
            request=request,
            name="shutdown.html",
            context={"shutdown_started": False, "message": "Solicitud no permitida."},
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

    shutdown_callback()
    return templates.TemplateResponse(
        request=request,
        name="shutdown.html",
        context={
            "shutdown_started": True,
            "message": "El sistema se está apagando correctamente.",
        },
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

