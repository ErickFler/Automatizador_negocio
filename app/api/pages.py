from datetime import date
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import DATA_ROOT
from app.db.database import get_db
from app.db.models import Branch, ImportBatch, ProcessingIssue, RawSaleRecord
from app.presentation.templates import templates
from app.services.dashboard_service import (
    METRIC_LABELS,
    PERIODS,
    DashboardInputError,
    build_dashboard,
    with_input_error,
)
from app.services.duplicate_service import DuplicateImportError
from app.services.product_catalog import list_products_for_inspection
from app.services.product_mapping import (
    create_and_map_product,
    list_pending_products,
    map_to_existing_product,
)
from app.services.product_matching import (
    find_product_candidates,
    search_master_products,
)
from app.services.raw_import_service import (
    FAILED,
    WAITING_FOR_PRODUCT_MAPPING,
    import_raw_sales_file,
)
from app.services.sales_processing_service import (
    get_batch_sales_summary,
    process_normalized_sales_batch,
)

router = APIRouter()
UPLOAD_DIRECTORY = DATA_ROOT / "uploads"
STATUS_LABELS = {
    "PENDING": "Pendiente",
    "PROCESSING": "Procesando",
    "COMPLETED": "Completada",
    "COMPLETED_WITH_WARNINGS": "Completada con incidencias",
    "WAITING_FOR_PRODUCT_MAPPING": "Productos por confirmar",
    "FAILED": "Fallida",
}


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    period: str = "this_month",
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "total_units",
    sort: str = "sales_amount",
    direction: str = "desc",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    status_code = status.HTTP_200_OK
    try:
        dashboard = build_dashboard(
            session,
            period=period,
            date_from=date_from,
            date_to=date_to,
            branch=branch,
            category=category,
            product=product,
            metric=metric,
            sort_by=sort,
            direction=direction,
        )
    except DashboardInputError as exc:
        dashboard = with_input_error(build_dashboard(session), str(exc))
        status_code = status.HTTP_400_BAD_REQUEST
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "dashboard": dashboard,
            "periods": PERIODS,
            "metric_labels": METRIC_LABELS,
        },
        status_code=status_code,
    )


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
            if result.status not in {WAITING_FOR_PRODUCT_MAPPING, FAILED}:
                process_normalized_sales_batch(session, batch_id=result.batch_id)
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

    return _render_import_result(request, session, batch_id=result.batch_id)


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


@router.get("/imports/{batch_id}", response_class=HTMLResponse)
def import_result(
    request: Request, batch_id: int, session: Session = Depends(get_db)
) -> HTMLResponse:
    return _render_import_result(request, session, batch_id=batch_id)


@router.get("/imports/{batch_id}/products", response_class=HTMLResponse)
def map_import_products(
    request: Request,
    batch_id: int,
    search: str = "",
    saved: bool = False,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render_product_mapping(
        request,
        session,
        batch_id=batch_id,
        search=search,
        saved=saved,
    )


@router.post("/imports/{batch_id}/products/map")
def confirm_existing_product(
    request: Request,
    batch_id: int,
    external_product_id: Annotated[str, Form()],
    master_product_id: Annotated[int, Form()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        map_to_existing_product(
            session,
            batch_id=batch_id,
            external_product_id=external_product_id,
            master_product_id=master_product_id,
        )
    except ValueError as exc:
        session.rollback()
        return _render_product_mapping(
            request,
            session,
            batch_id=batch_id,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    return RedirectResponse(
        url=(
            f"/imports/{batch_id}/products?saved=true"
            if pending
            else f"/imports/{batch_id}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/imports/{batch_id}/products/create")
def confirm_new_product(
    request: Request,
    batch_id: int,
    external_product_id: Annotated[str, Form()],
    master_display_name: Annotated[str, Form()],
    category_display_name: Annotated[str, Form()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        create_and_map_product(
            session,
            batch_id=batch_id,
            external_product_id=external_product_id,
            master_display_name=master_display_name,
            category_display_name=category_display_name,
        )
    except ValueError as exc:
        session.rollback()
        return _render_product_mapping(
            request,
            session,
            batch_id=batch_id,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    return RedirectResponse(
        url=(
            f"/imports/{batch_id}/products?saved=true"
            if pending
            else f"/imports/{batch_id}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
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


def _render_import_result(
    request: Request, session: Session, *, batch_id: int
) -> HTMLResponse:
    batch = session.scalar(
        select(ImportBatch)
        .options(
            selectinload(ImportBatch.branch),
            selectinload(ImportBatch.processing_issues).selectinload(
                ProcessingIssue.raw_sale_record
            ),
        )
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        return templates.TemplateResponse(
            request=request,
            name="import_result.html",
            context={"batch": None, "status_labels": STATUS_LABELS},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request=request,
        name="import_result.html",
        context={
            "batch": batch,
            "summary": get_batch_sales_summary(session, batch_id=batch_id),
            "status_labels": STATUS_LABELS,
        },
    )


def _render_product_mapping(
    request: Request,
    session: Session,
    *,
    batch_id: int,
    search: str = "",
    saved: bool = False,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    batch = session.scalar(
        select(ImportBatch)
        .options(selectinload(ImportBatch.branch))
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        return templates.TemplateResponse(
            request=request,
            name="product_mapping.html",
            context={"batch": None, "pending": []},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    current = pending[0] if pending else None
    suggestions = (
        find_product_candidates(
            session,
            original_name=current.original_product_name,
            original_category_name=current.original_category_name,
        )
        if current
        else []
    )
    manual_results = (
        search_master_products(session, query=search) if search.strip() else []
    )
    all_external_ids = {
        (external_id or "").strip()
        for external_id in session.scalars(
            select(RawSaleRecord.external_product_id).where(
                RawSaleRecord.import_batch_id == batch_id
            )
        ).all()
        if (external_id or "").strip()
    }
    resolved_count = len(all_external_ids) - len(pending)
    return templates.TemplateResponse(
        request=request,
        name="product_mapping.html",
        context={
            "batch": batch,
            "pending": pending,
            "current": current,
            "suggestions": suggestions,
            "manual_results": manual_results,
            "search": search,
            "saved": saved,
            "error": error,
            "current_position": resolved_count + 1 if current else resolved_count,
            "total_products": len(all_external_ids),
        },
        status_code=status_code,
    )
