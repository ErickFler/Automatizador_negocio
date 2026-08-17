from datetime import date
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import DATA_ROOT
from app.db.database import get_db
from app.db.models import MasterProduct
from app.presentation.templates import templates
from app.services.cost_service import (
    DuplicateCostImportError,
    get_cost_import_batch,
    get_effective_cost,
    import_cost_file,
    list_cost_overview,
    list_pending_cost_rows,
    map_cost_row,
    product_cost_history,
    skip_cost_row,
)
from app.services.product_matching import find_product_candidates, search_master_products

router = APIRouter(prefix="/costs", tags=["costs"])
COST_UPLOAD_DIRECTORY = DATA_ROOT / "cost_uploads"
COST_ROW_STATUS_LABELS = {
    "PENDING_MAPPING": "Por confirmar",
    "READY": "Lista para importar",
    "WARNING": "Requiere revisión",
    "INVALID": "No importada",
    "CONFLICT": "Conflicto",
    "IMPORTED": "Importada",
    "SKIPPED": "Omitida",
}


@router.get("", response_class=HTMLResponse)
def costs_index(
    request: Request,
    effective_on: str = "",
    search: str = "",
    missing_only: bool = False,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    reference_date, warning = _safe_date(effective_on, date.today())
    all_rows = list_cost_overview(session, reference_date=reference_date)
    rows = list_cost_overview(
        session,
        reference_date=reference_date,
        search=search,
        missing_only=missing_only,
    )
    return templates.TemplateResponse(
        request=request,
        name="costs/index.html",
        context={
            "rows": rows,
            "reference_date": reference_date,
            "search": search,
            "missing_only": missing_only,
            "products_with_cost": sum(row.effective_cost is not None for row in all_rows),
            "products_without_cost": sum(row.effective_cost is None for row in all_rows),
            "warning": warning,
        },
    )


@router.get("/import", response_class=HTMLResponse)
def cost_import_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="costs/import.html", context={"error": None}
    )


@router.post("/import", response_class=HTMLResponse)
def create_cost_import(
    request: Request,
    effective_from: Annotated[date, Form()],
    file: Annotated[UploadFile, File()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    original_filename = Path(file.filename or "").name
    if not original_filename:
        return templates.TemplateResponse(
            request=request,
            name="costs/import.html",
            context={"error": "Selecciona un archivo Excel."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        with TemporaryDirectory(prefix="cost-import-") as temporary_directory:
            temporary_path = Path(temporary_directory) / (
                f"costos{Path(original_filename).suffix.lower()}"
            )
            with temporary_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)
            result = import_cost_file(
                session,
                file_path=temporary_path,
                original_filename=original_filename,
                effective_from=effective_from,
                upload_directory=COST_UPLOAD_DIRECTORY,
            )
    except DuplicateCostImportError as exc:
        return templates.TemplateResponse(
            request=request,
            name="costs/import.html",
            context={"error": str(exc)},
            status_code=status.HTTP_409_CONFLICT,
        )
    finally:
        file.file.close()
    return RedirectResponse(
        url=f"/costs/imports/{result.batch_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/imports/{batch_id}", response_class=HTMLResponse)
def cost_import_result(
    request: Request, batch_id: int, session: Session = Depends(get_db)
) -> HTMLResponse:
    batch = get_cost_import_batch(session, batch_id=batch_id)
    return templates.TemplateResponse(
        request=request,
        name="costs/result.html",
        context={"batch": batch, "row_status_labels": COST_ROW_STATUS_LABELS},
    )


@router.get("/imports/{batch_id}/products", response_class=HTMLResponse)
def cost_product_mapping(
    request: Request,
    batch_id: int,
    search: str = "",
    error: str = "",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    batch = get_cost_import_batch(session, batch_id=batch_id)
    pending = list_pending_cost_rows(session, batch_id=batch_id) if batch else []
    current = pending[0] if pending else None
    suggestions = (
        find_product_candidates(
            session,
            original_name=current.original_product_name or "",
            original_category_name=None,
        )
        if current
        else []
    )
    manual_results = search_master_products(session, query=search) if search else []
    return templates.TemplateResponse(
        request=request,
        name="costs/mapping.html",
        context={
            "batch": batch,
            "pending": pending,
            "current": current,
            "suggestions": suggestions,
            "manual_results": manual_results,
            "search": search,
            "error": error,
        },
    )


@router.post("/imports/{batch_id}/products/map")
def confirm_cost_product(
    batch_id: int,
    row_id: Annotated[int, Form()],
    master_product_id: Annotated[int, Form()],
    session: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        result = map_cost_row(
            session, row_id=row_id, master_product_id=master_product_id
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/costs/imports/{batch_id}/products?error={str(exc)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    target = (
        f"/costs/imports/{batch_id}/products"
        if result.pending_rows
        else f"/costs/imports/{batch_id}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/imports/{batch_id}/products/skip")
def omit_cost_product(
    batch_id: int,
    row_id: Annotated[int, Form()],
    session: Session = Depends(get_db),
) -> RedirectResponse:
    result = skip_cost_row(session, row_id=row_id)
    target = (
        f"/costs/imports/{batch_id}/products"
        if result.pending_rows
        else f"/costs/imports/{batch_id}"
    )
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/products/{master_product_id}", response_class=HTMLResponse)
def product_cost_detail(
    request: Request,
    master_product_id: int,
    effective_on: str = "",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    product = session.scalar(
        select(MasterProduct)
        .options(joinedload(MasterProduct.category))
        .where(MasterProduct.id == master_product_id, MasterProduct.active.is_(True))
    )
    if product is None:
        return templates.TemplateResponse(
            request=request,
            name="costs/detail.html",
            context={"product": None},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    reference_date, warning = _safe_date(effective_on, date.today())
    return templates.TemplateResponse(
        request=request,
        name="costs/detail.html",
        context={
            "product": product,
            "reference_date": reference_date,
            "effective_cost": get_effective_cost(
                session, master_product_id=product.id, sales_date=reference_date
            ),
            "history": product_cost_history(session, master_product_id=product.id),
            "warning": warning,
        },
    )


def _safe_date(raw: str, default: date) -> tuple[date, str | None]:
    if not raw:
        return default, None
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return default, "La fecha no era válida; se utilizó el día de hoy."
