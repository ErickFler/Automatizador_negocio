from dataclasses import dataclass
from datetime import date, datetime
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DATA_ROOT
from app.db.models import (
    Branch,
    BranchProduct,
    ImportBatch,
    ProcessingIssue,
    RawSaleRecord,
)
from app.domain.normalization import normalize_for_comparison
from app.domain.import_policy import requires_product_mapping
from app.services.duplicate_service import ensure_import_is_not_duplicate
from app.services.excel_reader import (
    ExcelValidationError,
    ExcelWarning,
    read_sales_excel,
)
from app.services.file_storage import calculate_sha256, store_original_file

logger = logging.getLogger(__name__)

PENDING = "PENDING"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
WAITING_FOR_PRODUCT_MAPPING = "WAITING_FOR_PRODUCT_MAPPING"
FAILED = "FAILED"

WARNING = "WARNING"
ERROR = "ERROR"

DEFAULT_UPLOAD_DIRECTORY = DATA_ROOT / "uploads"


@dataclass(frozen=True, slots=True)
class RawImportResult:
    batch_id: int
    status: str
    total_rows: int
    normalized_rows: int
    issue_rows: int
    skipped_rows: int
    issue_count: int


def import_raw_sales_file(
    session: Session,
    *,
    file_path: str | Path,
    original_filename: str,
    sales_date: date,
    branch: Branch,
    upload_directory: str | Path = DEFAULT_UPLOAD_DIRECTORY,
) -> RawImportResult:
    """Valida y conserva una carga sin homologar productos ni calcular ventas."""
    if branch.id is None or not branch.active:
        raise ValueError("La sucursal seleccionada no está disponible.")

    source_path = Path(file_path)
    sha256 = calculate_sha256(source_path)
    ensure_import_is_not_duplicate(
        session,
        sha256=sha256,
        sales_date=sales_date,
        branch_id=branch.id,
    )
    stored_file = store_original_file(
        source_path,
        original_filename=original_filename,
        upload_directory=upload_directory,
    )
    batch = ImportBatch(
        sales_date=sales_date,
        branch=branch,
        source_filename=stored_file.original_filename,
        stored_filename=stored_file.stored_filename,
        sha256=sha256,
        status=PENDING,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)

    try:
        batch.status = PROCESSING
        session.commit()
        read_result = read_sales_excel(source_path)
    except ExcelValidationError as exc:
        session.rollback()
        _mark_failed(session, batch.id, issue_type=exc.code, message=str(exc))
        return RawImportResult(
            batch_id=batch.id,
            status=FAILED,
            total_rows=0,
            normalized_rows=0,
            issue_rows=0,
            skipped_rows=0,
            issue_count=1,
        )
    except Exception:
        session.rollback()
        _mark_failed(
            session,
            batch.id,
            issue_type="UNEXPECTED_IMPORT_ERROR",
            message="No fue posible procesar el archivo.",
        )
        logger.exception("Error inesperado en la carga cruda %s", batch.id)
        raise

    try:
        issue_rows: set[int] = set()
        issue_count = 0
        unknown_external_ids: set[str] = set()
        warnings_by_row: dict[int, list[ExcelWarning]] = {}
        for warning in read_result.warnings:
            warnings_by_row.setdefault(warning.row_number, []).append(warning)
        known_products = {
            product.external_product_id: product
            for product in session.scalars(
                select(BranchProduct).where(BranchProduct.branch_id == branch.id)
            ).all()
        }

        with session.begin_nested():
            for row in read_result.rows:
                raw_record = RawSaleRecord(
                    import_batch=batch,
                    source_row_number=row.source_row_number,
                    original_category_name=_raw_text(
                        row.original_values["NombreCategoria"]
                    ),
                    external_product_id=_raw_text(
                        row.original_values["IDProducto"]
                    ),
                    original_product_name=_raw_text(
                        row.original_values["NombreProducto"]
                    ),
                    quantity_raw=_raw_text(row.original_values["Cantidad"]),
                    sale_price_raw=_raw_text(row.original_values["PrecioVenta"]),
                    cost_raw=_raw_text(row.original_values["Costo"]),
                    amount_raw=_raw_text(row.original_values["Importe"]),
                    total_amount_raw=_raw_text(row.original_values["ImporteTotal"]),
                )
                session.add(raw_record)
                session.flush()

                for warning in warnings_by_row.get(row.source_row_number, []):
                    session.add(
                        ProcessingIssue(
                            import_batch=batch,
                            raw_sale_record=raw_record,
                            source_row_number=row.source_row_number,
                            issue_type=warning.warning_type,
                            severity=_severity_for(warning.warning_type),
                            message=warning.description,
                        )
                    )
                    issue_rows.add(row.source_row_number)
                    issue_count += 1

                external_id = (row.external_product_id or "").strip()
                known_product = known_products.get(external_id)
                if (
                    external_id
                    and known_product is None
                    and requires_product_mapping(row.sale_price)
                ):
                    unknown_external_ids.add(external_id)
                elif known_product is not None:
                    change_issues = _known_product_change_issues(
                        row_product_name=row.product_name,
                        row_category_name=row.category_name,
                        known_product=known_product,
                    )
                    for issue_type, message in change_issues:
                        session.add(
                            ProcessingIssue(
                                import_batch=batch,
                                raw_sale_record=raw_record,
                                source_row_number=row.source_row_number,
                                issue_type=issue_type,
                                severity=WARNING,
                                message=message,
                            )
                        )
                        issue_rows.add(row.source_row_number)
                        issue_count += 1

            batch.total_rows = read_result.total_rows
            batch.normalized_rows = 0
            batch.issue_rows = len(issue_rows)
            batch.skipped_rows = 0
            if unknown_external_ids:
                batch.status = WAITING_FOR_PRODUCT_MAPPING
            else:
                batch.status = COMPLETED_WITH_WARNINGS if issue_count else COMPLETED
            batch.error_message = None

        session.commit()
    except Exception:
        session.rollback()
        _mark_failed(
            session,
            batch.id,
            issue_type="RAW_PERSISTENCE_ERROR",
            message="No fue posible conservar todas las filas del archivo.",
        )
        logger.exception("Falló la persistencia cruda del lote %s", batch.id)
        raise

    logger.info(
        "Carga cruda completada: lote=%s filas=%s incidencias=%s",
        batch.id,
        batch.total_rows,
        issue_count,
    )
    return RawImportResult(
        batch_id=batch.id,
        status=batch.status,
        total_rows=batch.total_rows,
        normalized_rows=batch.normalized_rows,
        issue_rows=batch.issue_rows,
        skipped_rows=batch.skipped_rows,
        issue_count=issue_count,
    )


def _mark_failed(
    session: Session,
    batch_id: int,
    *,
    issue_type: str,
    message: str,
) -> None:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise RuntimeError("No fue posible recuperar la carga fallida.")
    batch.status = FAILED
    batch.total_rows = 0
    batch.normalized_rows = 0
    batch.issue_rows = 0
    batch.skipped_rows = 0
    batch.error_message = message
    session.add(
        ProcessingIssue(
            import_batch=batch,
            issue_type=issue_type,
            severity=ERROR,
            message=message,
        )
    )
    session.commit()


def _raw_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _severity_for(issue_type: str) -> str:
    if issue_type in {
        "INVALID_NUMERIC_VALUE",
        "MISSING_PRODUCT_ID",
        "MISSING_PRODUCT_NAME",
    }:
        return ERROR
    return WARNING


def _known_product_change_issues(
    *,
    row_product_name: str | None,
    row_category_name: str | None,
    known_product: BranchProduct,
) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    if (
        row_product_name
        and normalize_for_comparison(row_product_name)
        != known_product.normalized_product_name
    ):
        issues.append(
            (
                "BRANCH_PRODUCT_NAME_CHANGED",
                "El nombre recibido es diferente del nombre previamente confirmado "
                "para este producto local.",
            )
        )
    if normalize_for_comparison(row_category_name or "") != normalize_for_comparison(
        known_product.original_category_name or ""
    ):
        issues.append(
            (
                "BRANCH_PRODUCT_CATEGORY_CHANGED",
                "La categoría recibida es diferente de la categoría previamente "
                "confirmada para este producto local.",
            )
        )
    return issues
