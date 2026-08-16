from dataclasses import dataclass
from datetime import date
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import ImportBatch, ProcessingWarning, SaleRecord
from app.domain.calculations import calculate_units
from app.services.duplicate_service import ensure_import_is_not_duplicate
from app.services.excel_reader import ExcelSaleRow, ExcelWarning, read_sales_excel
from app.services.file_storage import (
    StoredOriginalFile,
    calculate_sha256,
    store_original_file,
)
from app.services.product_catalog import CatalogWarning, register_product

logger = logging.getLogger(__name__)

PENDING = "PENDING"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SalesProcessingResult:
    batch_id: int
    status: str
    total_rows: int
    valid_rows: int
    warning_rows: int
    warning_count: int
    new_product_ids: tuple[int, ...]


def process_sales_file(
    session: Session,
    *,
    file_path: str | Path,
    sales_date: date,
    branch: str,
    upload_directory: str | Path | None = None,
    original_filename: str | None = None,
) -> SalesProcessingResult:
    """Procesa y persiste un archivo completo, sin dejar ventas parciales."""
    normalized_branch = branch.strip()
    if not normalized_branch:
        raise ValueError("branch no puede estar vacío")

    source_path = Path(file_path)
    read_result = read_sales_excel(source_path)
    file_sha256 = calculate_sha256(source_path)
    ensure_import_is_not_duplicate(
        session,
        file_sha256=file_sha256,
        sales_date=sales_date,
        branch=normalized_branch,
    )

    stored_file = (
        store_original_file(source_path, original_filename=original_filename)
        if upload_directory is None
        else store_original_file(
            source_path,
            original_filename=original_filename,
            upload_directory=upload_directory,
        )
    )

    try:
        batch = _create_pending_batch(
            session,
            stored_file=stored_file,
            sales_date=sales_date,
            branch=normalized_branch,
            total_rows=read_result.total_rows,
        )
    except Exception:
        stored_file.stored_path.unlink(missing_ok=True)
        raise

    batch_id = batch.id
    try:
        batch.status = PROCESSING
        session.commit()

        batch = session.get(ImportBatch, batch_id)
        if batch is None:
            raise RuntimeError("No fue posible recuperar el lote creado.")

        new_product_ids: list[int] = []
        warning_row_numbers: set[int] = set()
        warning_count = 0
        valid_rows = 0

        with session.begin_nested():
            for warning in read_result.warnings:
                _persist_excel_warning(session, batch, warning)
                warning_row_numbers.add(warning.row_number)
                warning_count += 1

            for row in read_result.rows:
                if (
                    not row.external_product_id
                    or not row.product_name
                    or not row.product_name.strip()
                ):
                    continue

                catalog_result = register_product(
                    session,
                    external_product_id=row.external_product_id,
                    product_name=row.product_name,
                    category_name=row.category_name,
                )
                if catalog_result.product_created:
                    new_product_ids.append(catalog_result.product.id)

                for warning in catalog_result.warnings:
                    _persist_catalog_warning(session, batch, row, warning)
                    warning_row_numbers.add(row.source_row_number)
                    warning_count += 1

                calculation = calculate_units(
                    quantity=row.quantity,
                    sale_price=row.sale_price,
                    total_amount=row.total_amount,
                )
                for warning in calculation.warnings:
                    session.add(
                        ProcessingWarning(
                            batch=batch,
                            row_number=row.source_row_number,
                            product_id=row.external_product_id,
                            product_name=row.product_name,
                            warning_type=warning.warning_type,
                            description=warning.description,
                            original_values=_serialize_values(row.original_values),
                        )
                    )
                    warning_row_numbers.add(row.source_row_number)
                    warning_count += 1

                session.add(
                    SaleRecord(
                        batch=batch,
                        product=catalog_result.product,
                        sales_date=sales_date,
                        branch=normalized_branch,
                        quantity=row.quantity,
                        sale_price=row.sale_price,
                        unit_cost=row.unit_cost,
                        cost_amount=row.cost_amount,
                        total_amount=row.total_amount,
                        real_units=calculation.real_units,
                        combo_units=calculation.combo_units,
                        source_row_number=row.source_row_number,
                    )
                )
                valid_rows += 1

            batch.valid_rows = valid_rows
            batch.warning_rows = len(warning_row_numbers)
            batch.status = COMPLETED_WITH_WARNINGS if warning_count else COMPLETED

        session.commit()
    except Exception:
        session.rollback()
        _mark_batch_failed(session, batch_id)
        logger.exception("Falló el procesamiento del lote %s", batch_id)
        raise

    logger.info(
        "Lote procesado: %s | estado=%s | filas=%s | válidas=%s",
        batch_id,
        batch.status,
        batch.total_rows,
        batch.valid_rows,
    )
    return SalesProcessingResult(
        batch_id=batch_id,
        status=batch.status,
        total_rows=batch.total_rows,
        valid_rows=batch.valid_rows,
        warning_rows=batch.warning_rows,
        warning_count=warning_count,
        new_product_ids=tuple(new_product_ids),
    )


def _create_pending_batch(
    session: Session,
    *,
    stored_file: StoredOriginalFile,
    sales_date: date,
    branch: str,
    total_rows: int,
) -> ImportBatch:
    batch = ImportBatch(
        sales_date=sales_date,
        branch=branch,
        source_filename=stored_file.original_filename,
        stored_filename=stored_file.stored_filename,
        file_sha256=stored_file.sha256,
        total_rows=total_rows,
        status=PENDING,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def _mark_batch_failed(session: Session, batch_id: int) -> None:
    failed_batch = session.get(ImportBatch, batch_id)
    if failed_batch is None:
        return
    failed_batch.status = FAILED
    failed_batch.valid_rows = 0
    failed_batch.warning_rows = 0
    session.commit()


def _persist_excel_warning(
    session: Session, batch: ImportBatch, warning: ExcelWarning
) -> None:
    session.add(
        ProcessingWarning(
            batch=batch,
            row_number=warning.row_number,
            product_id=warning.product_id,
            product_name=warning.product_name,
            warning_type=warning.warning_type,
            description=warning.description,
            original_values=_serialize_values(warning.original_values),
        )
    )


def _persist_catalog_warning(
    session: Session,
    batch: ImportBatch,
    row: ExcelSaleRow,
    warning: CatalogWarning,
) -> None:
    changed_values = {
        "previous": warning.previous_values,
        "new": warning.new_values,
        "source": row.original_values,
    }
    session.add(
        ProcessingWarning(
            batch=batch,
            row_number=row.source_row_number,
            product_id=row.external_product_id,
            product_name=row.product_name,
            warning_type=warning.warning_type,
            description=warning.description,
            original_values=_serialize_values(changed_values),
        )
    )


def _serialize_values(values: dict[str, object | None]) -> str:
    return json.dumps(values, ensure_ascii=False, default=str, sort_keys=True)
