from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
import logging
from pathlib import Path
import re
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.config import DATA_ROOT
from app.db.models import (
    Category,
    ImportBatch,
    MasterProduct as Product,
    ProcessingIssue as ProcessingWarning,
    SaleRecord,
)
from app.domain.normalization import PENDING_CATEGORY_NAME
from app.services.consolidation_service import (
    ConsolidationFilters,
    consolidate_sales,
)
from app.services.sales_processor import COMPLETED, COMPLETED_WITH_WARNINGS

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_DIRECTORY = DATA_ROOT / "exports"
QUANTITY_FORMAT = "#,##0.######"
MONEY_FORMAT = '$#,##0.00'
DATE_FORMAT = "dd/mm/yyyy"
DATETIME_FORMAT = "dd/mm/yyyy hh:mm"
_UNSAFE_FILENAME_CHARACTER = re.compile(r"[^\w.-]", flags=re.UNICODE)
_COMPLETED_STATUSES = (COMPLETED, COMPLETED_WITH_WARNINGS)


def export_sales_workbook(
    session: Session,
    *,
    filters: ConsolidationFilters | None = None,
    export_directory: str | Path = DEFAULT_EXPORT_DIRECTORY,
    filename: str | None = None,
) -> Path:
    """Genera un Excel nuevo con concentrado, detalle, incidencias y altas."""
    selected_filters = filters or ConsolidationFilters()
    destination = _build_destination(export_directory, filename)

    workbook = Workbook()
    concentrate_sheet = workbook.active
    concentrate_sheet.title = "Concentrado"
    detail_sheet = workbook.create_sheet("Detalle")
    warning_sheet = workbook.create_sheet("Incidencias")
    new_products_sheet = workbook.create_sheet("Productos Nuevos")

    _write_concentrate(concentrate_sheet, session, selected_filters)
    _write_detail(detail_sheet, session, selected_filters)
    _write_warnings(warning_sheet, session, selected_filters)
    _write_new_products(new_products_sheet, session, selected_filters)

    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    logger.info("Concentrado exportado: %s", destination.name)
    return destination


def _write_concentrate(
    sheet: Worksheet, session: Session, filters: ConsolidationFilters
) -> None:
    headers = (
        "Fecha",
        "Sucursal",
        "Categoria",
        "IDProducto",
        "Producto",
        "Cantidad Registrada",
        "Unidades Reales",
        "Unidades Combo",
        "Importe Total",
    )
    rows = (
        (
            item.sales_date,
            item.branch,
            item.category,
            item.external_product_id,
            item.product,
            item.registered_quantity,
            item.real_units,
            item.combo_units,
            item.total_amount,
        )
        for item in consolidate_sales(session, filters)
    )
    _write_sheet(sheet, headers, rows)
    _format_columns(
        sheet,
        date_columns=(1,),
        quantity_columns=(6, 7, 8),
        money_columns=(9,),
    )


def _write_detail(
    sheet: Worksheet, session: Session, filters: ConsolidationFilters
) -> None:
    category_name = func.coalesce(Category.name, PENDING_CATEGORY_NAME)
    statement = (
        select(
            SaleRecord.sales_date,
            SaleRecord.branch,
            ImportBatch.source_filename,
            ImportBatch.id.label("batch_id"),
            SaleRecord.source_row_number,
            category_name.label("category"),
            Product.external_product_id,
            Product.original_name,
            SaleRecord.quantity,
            SaleRecord.sale_price,
            SaleRecord.unit_cost,
            SaleRecord.cost_amount,
            SaleRecord.total_amount,
            SaleRecord.real_units,
            SaleRecord.combo_units,
        )
        .join(Product, Product.id == SaleRecord.product_id)
        .join(ImportBatch, ImportBatch.id == SaleRecord.batch_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(ImportBatch.status.in_(_COMPLETED_STATUSES))
        .order_by(
            SaleRecord.sales_date,
            SaleRecord.branch,
            category_name,
            Product.original_name,
            SaleRecord.source_row_number,
        )
    )
    statement = _apply_sale_filters(statement, filters)
    headers = (
        "Fecha",
        "Sucursal",
        "Archivo Origen",
        "Batch ID",
        "Fila Origen",
        "Categoria",
        "IDProducto",
        "Producto",
        "Cantidad",
        "PrecioVenta",
        "Costo",
        "Importe",
        "ImporteTotal",
        "UnidadesReales",
        "UnidadesCombo",
    )
    _write_sheet(sheet, headers, session.execute(statement))
    _format_columns(
        sheet,
        date_columns=(1,),
        quantity_columns=(9, 14, 15),
        money_columns=(10, 11, 12, 13),
    )


def _write_warnings(
    sheet: Worksheet, session: Session, filters: ConsolidationFilters
) -> None:
    statement = (
        select(
            ImportBatch.id.label("batch_id"),
            ImportBatch.sales_date,
            ImportBatch.branch,
            ImportBatch.source_filename,
            ProcessingWarning.row_number,
            ProcessingWarning.product_id,
            ProcessingWarning.product_name,
            ProcessingWarning.warning_type,
            ProcessingWarning.description,
            ProcessingWarning.original_values,
        )
        .join(ImportBatch, ImportBatch.id == ProcessingWarning.batch_id)
        .outerjoin(
            Product,
            Product.external_product_id == ProcessingWarning.product_id,
        )
        .outerjoin(Category, Category.id == Product.category_id)
        .where(ImportBatch.status.in_(_COMPLETED_STATUSES))
        .order_by(ImportBatch.id, ProcessingWarning.row_number, ProcessingWarning.id)
    )
    statement = _apply_batch_filters(statement, filters)
    statement = _apply_product_filters(statement, filters)
    headers = (
        "Batch ID",
        "Fecha",
        "Sucursal",
        "Archivo Origen",
        "Fila",
        "IDProducto",
        "Producto",
        "Tipo",
        "Descripcion",
        "Valores Originales",
    )
    _write_sheet(sheet, headers, session.execute(statement))
    _format_columns(sheet, date_columns=(2,))


def _write_new_products(
    sheet: Worksheet, session: Session, filters: ConsolidationFilters
) -> None:
    first_batch = (
        select(
            SaleRecord.product_id.label("product_id"),
            func.min(SaleRecord.batch_id).label("first_batch_id"),
        )
        .group_by(SaleRecord.product_id)
        .subquery()
    )
    category_name = func.coalesce(Category.name, PENDING_CATEGORY_NAME)
    statement = (
        select(
            ImportBatch.id.label("batch_id"),
            ImportBatch.sales_date,
            ImportBatch.branch,
            Product.external_product_id,
            Product.original_name,
            category_name.label("category"),
            Product.created_at,
        )
        .join(first_batch, first_batch.c.product_id == Product.id)
        .join(ImportBatch, ImportBatch.id == first_batch.c.first_batch_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(ImportBatch.status.in_(_COMPLETED_STATUSES))
        .order_by(ImportBatch.id, category_name, Product.original_name)
    )
    statement = _apply_batch_filters(statement, filters)
    statement = _apply_product_filters(statement, filters)
    headers = (
        "Batch ID",
        "Fecha",
        "Sucursal",
        "IDProducto",
        "Producto",
        "Categoria",
        "Fecha Alta",
    )
    _write_sheet(sheet, headers, session.execute(statement))
    _format_columns(sheet, date_columns=(2,), datetime_columns=(7,))


def _apply_sale_filters(
    statement: Select[tuple[object, ...]], filters: ConsolidationFilters
) -> Select[tuple[object, ...]]:
    statement = _apply_batch_filters(statement, filters)
    statement = _apply_product_filters(statement, filters)
    return statement


def _apply_batch_filters(
    statement: Select[tuple[object, ...]], filters: ConsolidationFilters
) -> Select[tuple[object, ...]]:
    if filters.sales_date_from is not None:
        statement = statement.where(ImportBatch.sales_date >= filters.sales_date_from)
    if filters.sales_date_to is not None:
        statement = statement.where(ImportBatch.sales_date <= filters.sales_date_to)
    if filters.branches:
        branches = tuple(branch.strip() for branch in filters.branches)
        statement = statement.where(ImportBatch.branch.in_(branches))
    if filters.batch_ids:
        statement = statement.where(ImportBatch.id.in_(filters.batch_ids))
    return statement


def _apply_product_filters(
    statement: Select[tuple[object, ...]], filters: ConsolidationFilters
) -> Select[tuple[object, ...]]:
    if filters.category_ids:
        statement = statement.where(Category.id.in_(filters.category_ids))
    if filters.product_ids:
        statement = statement.where(Product.id.in_(filters.product_ids))
    return statement


def _write_sheet(
    sheet: Worksheet,
    headers: tuple[str, ...],
    rows: Iterable[Sequence[object]],
) -> None:
    sheet.append(headers)
    for row in rows:
        sheet.append(tuple(_excel_safe_value(value) for value in row))
    _format_header_and_layout(sheet)


def _format_header_and_layout(sheet: Worksheet) -> None:
    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    for column_cells in sheet.columns:
        maximum_length = max(
            len(str(cell.value)) if cell.value is not None else 0
            for cell in column_cells
        )
        letter = get_column_letter(column_cells[0].column)
        sheet.column_dimensions[letter].width = min(max(maximum_length + 2, 10), 50)


def _format_columns(
    sheet: Worksheet,
    *,
    date_columns: tuple[int, ...] = (),
    datetime_columns: tuple[int, ...] = (),
    quantity_columns: tuple[int, ...] = (),
    money_columns: tuple[int, ...] = (),
) -> None:
    for column in date_columns:
        _set_number_format(sheet, column, DATE_FORMAT)
    for column in datetime_columns:
        _set_number_format(sheet, column, DATETIME_FORMAT)
    for column in quantity_columns:
        _set_number_format(sheet, column, QUANTITY_FORMAT)
    for column in money_columns:
        _set_number_format(sheet, column, MONEY_FORMAT)


def _set_number_format(sheet: Worksheet, column: int, number_format: str) -> None:
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row=row, column=column).number_format = number_format


def _excel_safe_value(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _build_destination(
    export_directory: str | Path, filename: str | None
) -> Path:
    directory = Path(export_directory)
    requested_name = filename or f"concentrado_{uuid4().hex}.xlsx"
    safe_name = _UNSAFE_FILENAME_CHARACTER.sub("_", Path(requested_name).name)
    if not safe_name.lower().endswith(".xlsx"):
        safe_name = f"{safe_name}.xlsx"
    destination = directory / safe_name
    if destination.exists():
        raise FileExistsError(f"La exportación ya existe: {destination.name}")
    return destination
