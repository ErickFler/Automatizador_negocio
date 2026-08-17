from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.pages import router as pages_router
from app.api.reports import router as reports_router
from app.api.costs import router as costs_router
from app.api.system import router as system_router
from app.core.config import RESOURCE_ROOT, settings
from app.core.logging import configure_logging, get_logger
from app.db.database import initialize_database
from app.presentation.templates import templates


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger = get_logger(__name__)
    initialize_database()
    logger.info("Aplicacion iniciada")
    yield
    logger.info("Aplicacion detenida")


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.shutdown_callback = None
app.state.shutdown_token = ""
app.mount("/static", StaticFiles(directory=RESOURCE_ROOT / "app" / "static"), name="static")
app.include_router(pages_router)
app.include_router(reports_router)
app.include_router(costs_router)
app.include_router(system_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "application": settings.app_name}


def _error_page(
    request: Request, *, status_code: int, title: str, message: str
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"title": title, "message": message},
        status_code=status_code,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_page(
    request: Request, exception: StarletteHTTPException
) -> HTMLResponse:
    if exception.status_code == 404:
        return _error_page(
            request,
            status_code=404,
            title="Página no encontrada",
            message="La página o el registro solicitado no existe.",
        )
    return _error_page(
        request,
        status_code=exception.status_code,
        title="No pudimos completar la solicitud",
        message="Revise los datos e inténtelo nuevamente.",
    )


@app.exception_handler(RequestValidationError)
async def validation_error_page(
    request: Request, _exception: RequestValidationError
) -> HTMLResponse:
    return _error_page(
        request,
        status_code=422,
        title="Hay datos que debemos revisar",
        message="Verifique las fechas, la sucursal y los demás campos del formulario.",
    )


@app.exception_handler(Exception)
async def unexpected_error_page(request: Request, _exception: Exception) -> HTMLResponse:
    get_logger(__name__).exception("Error inesperado al atender %s", request.url.path)
    return _error_page(
        request,
        status_code=500,
        title="Ocurrió un problema inesperado",
        message="La información técnica quedó registrada. Inténtelo nuevamente.",
    )
