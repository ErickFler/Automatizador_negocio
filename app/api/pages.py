from datetime import date
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import DATA_ROOT, RESOURCE_ROOT
from app.db.database import get_db
from app.db.models import Branch, ImportBatch
from app.services.duplicate_service import DuplicateImportError
from app.services.product_catalog import list_products_for_inspection
from app.services.raw_import_service import import_raw_sales_file

TEMPLATE_DIRECTORY = RESOURCE_ROOT / "app" / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIRECTORY)
router = APIRouter()
UPLOAD_DIRECTORY = DATA_ROOT / "uploads"
STATUS_LABELS = {
    "PENDING": "Pendiente",
    "PROCESSING": "Procesando",
    "COMPLETED": "Completada",
    "COMPLETED_WITH_WARNINGS": "Completada con incidencias",
    "FAILED": "Fallida",
}


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/products", response_class=HTMLResponse)
def products(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    branches = session.scalars(select(Branch).order_by(Branch.id)).all()
    catalog = list_products_for_inspection(session)
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={"branches": branches, "products": catalog},
    )


@router.get("/imports/new", response_class=HTMLResponse)
def new_import(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    return _render_import_form(request, session)


@router.post("/imports", response_class=HTMLResponse)
def create_import(
    request: Request,
    sales_date: Annotated[date, Form()],
    branch_id: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    branch = session.scalar(
        select(Branch).where(Branch.id == branch_id, Branch.active.is_(True))
    )
    if branch is None:
        return _render_import_form(
            request,
            session,
            error="Selecciona una sucursal disponible.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    original_filename = Path(file.filename or "").name
    if not original_filename:
        return _render_import_form(
            request,
            session,
            error="Selecciona un archivo Excel.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with TemporaryDirectory(prefix="raw-sales-import-") as temporary_directory:
            temporary_path = Path(temporary_directory) / (
                f"reporte{Path(original_filename).suffix.lower()}"
            )
            with temporary_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            result = import_raw_sales_file(
                session,
                file_path=temporary_path,
                original_filename=original_filename,
                sales_date=sales_date,
                branch=branch,
                upload_directory=UPLOAD_DIRECTORY,
            )
    except DuplicateImportError as exc:
        session.rollback()
        return _render_import_form(
            request,
            session,
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    except (OSError, ValueError):
        session.rollback()
        return _render_import_form(
            request,
            session,
            error="No fue posible conservar el archivo seleccionado.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    finally:
        file.file.close()

    batch = session.scalar(
        select(ImportBatch)
        .options(
            selectinload(ImportBatch.branch),
            selectinload(ImportBatch.processing_issues),
        )
        .where(ImportBatch.id == result.batch_id)
    )
    return templates.TemplateResponse(
        request=request,
        name="import_result.html",
        context={"batch": batch, "status_labels": STATUS_LABELS},
    )


@router.get("/imports", response_class=HTMLResponse)
def import_history(
    request: Request, session: Session = Depends(get_db)
) -> HTMLResponse:
    batches = session.scalars(
        select(ImportBatch)
        .options(selectinload(ImportBatch.branch))
        .order_by(ImportBatch.imported_at.desc())
        .limit(100)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="imports_history.html",
        context={"batches": batches, "status_labels": STATUS_LABELS},
    )


def _render_import_form(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    branches = session.scalars(
        select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="import_new.html",
        context={"branches": branches, "error": error},
        status_code=status_code,
    )
