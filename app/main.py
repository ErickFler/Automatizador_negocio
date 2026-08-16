from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.pages import router as pages_router
from app.api.system import router as system_router
from app.core.config import RESOURCE_ROOT, settings
from app.core.logging import configure_logging, get_logger
from app.db.database import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger = get_logger(__name__)
    initialize_database()
    logger.info("Aplicacion iniciada")
    yield
    logger.info("Aplicacion detenida")


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=RESOURCE_ROOT / "app" / "static"), name="static")
app.include_router(pages_router)
app.include_router(system_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "application": settings.app_name}
