from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import DATA_ROOT, RESOURCE_ROOT, settings
from app.db.database import get_db
from app.db.models import ImportBatch, Product, SaleRecord
from app.services.consolidation_service import ConsolidationFilters
from app.services.duplicate_service import DuplicateImportError
from app.services.excel_reader import ExcelValidationError
from app.services.export_service import export_sales_workbook
from app.services.sales_processor import SalesProcessingResult, process_sales_file

logger = logging.getLogger(__name__)

TEMPLATE_DIRECTORY = RESOURCE_ROOT / "app" / "templates"
UPLOAD_DIRECTORY = DATA_ROOT / "uploads"
EXPORT_DIRECTORY = DATA_ROOT / "exports"
templates = Jinja2Templates(directory=TEMPLATE_DIRECTORY)
router = APIRouter()


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    filename: str
    branch: str
    successful: bool
    message: str
    result: SalesProcessingResult | None = None


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "branches": settings.branches,
            "today": date.today().isoformat(),
        },
    )


@router.post("/uploads", response_class=HTMLResponse)
def upload_sales_files(
    request: Request,
    sales_date: Annotated[date, Form()],
    branches: Annotated[list[str], Form()],
    files: Annotated[list[UploadFile], File()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    validation_error = _validate_upload_request(branches, files)
    if validation_error:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "branches": settings.branches,
                "today": sales_date.isoformat(),
                "error": validation_error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    outcomes: list[UploadOutcome] = []
    with TemporaryDirectory(prefix="sales-upload-") as temporary_directory:
        for position, (branch, upload) in enumerate(zip(branches, files, strict=True)):
            filename = Path(upload.filename or f"archivo_{position + 1}.xlsx").name
            temporary_path = Path(temporary_directory) / (
                f"{position}_{uuid4().hex}{Path(filename).suffix.lower()}"
            )
            try:
                with temporary_path.open("wb") as destination:
                    shutil.copyfileobj(upload.file, destination)
                result = process_sales_file(
                    session,
                    file_path=temporary_path,
                    original_filename=filename,
                    sales_date=sales_date,
                    branch=branch,
                    upload_directory=UPLOAD_DIRECTORY,
                )
                outcomes.append(
                    UploadOutcome(
                        filename=filename,
                        branch=branch,
                        successful=True,
                        message="Procesado correctamente",
                        result=result,
                    )
                )
            except (ExcelValidationError, DuplicateImportError, ValueError) as exc:
                session.rollback()
                outcomes.append(
                    UploadOutcome(
                        filename=filename,
                        branch=branch,
                        successful=False,
                        message=str(exc),
                    )
                )
            except Exception:
                session.rollback()
                logger.exception("Error inesperado al procesar %s", filename)
                outcomes.append(
                    UploadOutcome(
                        filename=filename,
                        branch=branch,
                        successful=False,
                        message="Ocurrió un error inesperado. Revisa el log local.",
                    )
                )
            finally:
                upload.file.close()

    successful_results = [
        outcome.result
        for outcome in outcomes
        if outcome.successful and outcome.result is not None
    ]
    batch_ids = tuple(result.batch_id for result in successful_results)
    export_filename = None
    if batch_ids:
        export_path = export_sales_workbook(
            session,
            filters=ConsolidationFilters(batch_ids=batch_ids),
            export_directory=EXPORT_DIRECTORY,
            filename=f"concentrado_{sales_date:%Y%m%d}_{uuid4().hex[:8]}.xlsx",
        )
        export_filename = export_path.name

    summary = _build_summary(session, successful_results)
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "sales_date": sales_date,
            "outcomes": outcomes,
            "summary": summary,
            "export_filename": export_filename,
        },
    )


@router.get("/exports/{filename}", response_class=FileResponse)
def download_export(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    export_path = EXPORT_DIRECTORY / safe_name
    if (
        safe_name != filename
        or export_path.suffix.lower() != ".xlsx"
        or not export_path.is_file()
    ):
        raise HTTPException(status_code=404, detail="Exportación no encontrada")
    return FileResponse(
        path=export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=safe_name,
    )


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    batches = session.scalars(
        select(ImportBatch).order_by(ImportBatch.imported_at.desc()).limit(100)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="history.html",
        context={"batches": batches},
    )


@router.get("/history/{batch_id}/export", response_class=FileResponse)
def export_history_batch(
    batch_id: int, session: Session = Depends(get_db)
) -> FileResponse:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
    export_path = export_sales_workbook(
        session,
        filters=ConsolidationFilters(batch_ids=(batch_id,)),
        export_directory=EXPORT_DIRECTORY,
        filename=f"concentrado_lote_{batch_id}_{uuid4().hex[:8]}.xlsx",
    )
    return FileResponse(
        path=export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=export_path.name,
    )


@router.get("/products", response_class=HTMLResponse)
def products(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    catalog = session.scalars(
        select(Product)
        .options(selectinload(Product.category))
        .order_by(Product.normalized_name)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={"products": catalog},
    )


def _validate_upload_request(
    branches: list[str], files: list[UploadFile]
) -> str | None:
    if not 1 <= len(files) <= 4:
        return "Debes seleccionar entre uno y cuatro archivos."
    if len(branches) != len(files):
        return "Cada archivo debe tener una sucursal asociada."
    normalized_branches = [branch.strip() for branch in branches]
    if any(branch not in settings.branches for branch in normalized_branches):
        return "Se recibió una sucursal no configurada."
    if len(set(normalized_branches)) != len(normalized_branches):
        return "No puedes repetir una sucursal en la misma operación."
    if any(not upload.filename for upload in files):
        return "Todos los archivos deben tener un nombre válido."
    return None


def _build_summary(
    session: Session, results: list[SalesProcessingResult]
) -> dict[str, object]:
    batch_ids = tuple(result.batch_id for result in results)
    totals = (None, None, None, None)
    if batch_ids:
        totals = session.execute(
            select(
                func.sum(SaleRecord.quantity),
                func.sum(SaleRecord.real_units),
                func.sum(SaleRecord.combo_units),
                func.sum(SaleRecord.total_amount),
            ).where(SaleRecord.batch_id.in_(batch_ids))
        ).one()
    quantity, real_units, combo_units, total_amount = totals
    return {
        "processed_files": len(results),
        "total_rows": sum(result.total_rows for result in results),
        "valid_rows": sum(result.valid_rows for result in results),
        "warnings": sum(result.warning_count for result in results),
        "new_products": len(
            {product_id for result in results for product_id in result.new_product_ids}
        ),
        "quantity": quantity if quantity is not None else Decimal("0"),
        "real_units": real_units if real_units is not None else Decimal("0"),
        "combo_units": combo_units if combo_units is not None else Decimal("0"),
        "total_amount": total_amount if total_amount is not None else Decimal("0"),
    }
