from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.config import DATA_ROOT
from app.db.models import (
    CostImportBatch,
    CostImportRow,
    MasterProduct,
    ProductCost,
    SaleRecord,
)
from app.domain.normalization import normalize_for_comparison
from app.services.file_storage import calculate_sha256, store_original_file

PENDING = "PENDING"
FAILED = "FAILED"
WAITING_FOR_PRODUCT_MAPPING = "WAITING_FOR_PRODUCT_MAPPING"
COMPLETED = "COMPLETED"
COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"

ROW_PENDING = "PENDING_MAPPING"
ROW_READY = "READY"
ROW_WARNING = "WARNING"
ROW_INVALID = "INVALID"
ROW_CONFLICT = "CONFLICT"
ROW_IMPORTED = "IMPORTED"
ROW_SKIPPED = "SKIPPED"

DEFAULT_COST_UPLOAD_DIRECTORY = DATA_ROOT / "cost_uploads"
SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm"}


class CostConflictError(ValueError):
    pass


class DuplicateCostImportError(ValueError):
    pass


class CostExcelValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CostExcelMapping:
    product_aliases: tuple[str, ...] = (
        "producto",
        "nombre producto",
        "nombreproducto",
    )
    cost_aliases: tuple[str, ...] = ("costo", "costo unitario", "costounitario")


@dataclass(frozen=True, slots=True)
class EffectiveCost:
    cost: Decimal
    effective_from: date


@dataclass(frozen=True, slots=True)
class CostImportResult:
    batch_id: int
    status: str
    total_rows: int
    imported_rows: int
    pending_rows: int
    warning_rows: int


@dataclass(frozen=True, slots=True)
class CostOverviewRow:
    product: MasterProduct
    effective_cost: EffectiveCost | None


@dataclass(frozen=True, slots=True)
class CostCoverage:
    products_with_cost: int
    products_without_cost: int
    sale_records_without_cost: int


def create_product_cost(
    session: Session,
    *,
    master_product_id: int,
    effective_from: date,
    cost: Decimal,
    source_filename: str | None = None,
    notes: str | None = None,
    commit: bool = True,
) -> ProductCost:
    product = session.get(MasterProduct, master_product_id)
    if product is None or not product.active:
        raise ValueError("Selecciona un producto maestro válido.")
    if not isinstance(cost, Decimal):
        raise TypeError("El costo debe proporcionarse como Decimal.")
    if not cost.is_finite() or cost < 0:
        raise ValueError("El costo debe ser un número mayor o igual a cero.")
    if session.scalar(
        select(ProductCost.id).where(
            ProductCost.master_product_id == master_product_id,
            ProductCost.effective_from == effective_from,
        )
    ) is not None:
        raise CostConflictError(
            "Ya existe un costo para este producto con la misma fecha de vigencia."
        )
    row = ProductCost(
        master_product=product,
        effective_from=effective_from,
        cost=cost,
        source_filename=source_filename,
        notes=notes,
    )
    session.add(row)
    if commit:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise CostConflictError(
                "Ya existe un costo para este producto con la misma fecha de vigencia."
            ) from exc
        session.refresh(row)
    return row


def get_effective_cost(
    session: Session, *, master_product_id: int, sales_date: date
) -> EffectiveCost | None:
    row = session.scalar(
        select(ProductCost)
        .where(
            ProductCost.master_product_id == master_product_id,
            ProductCost.effective_from <= sales_date,
        )
        .order_by(ProductCost.effective_from.desc())
        .limit(1)
    )
    return EffectiveCost(row.cost, row.effective_from) if row else None


def get_effective_costs(
    session: Session, requests: Iterable[tuple[int, date]]
) -> dict[tuple[int, date], EffectiveCost | None]:
    requested = tuple(set(requests))
    if not requested:
        return {}
    product_ids = {product_id for product_id, _ in requested}
    latest_date = max(sales_date for _, sales_date in requested)
    history: dict[int, list[ProductCost]] = {}
    for row in session.scalars(
        select(ProductCost)
        .where(
            ProductCost.master_product_id.in_(product_ids),
            ProductCost.effective_from <= latest_date,
        )
        .order_by(ProductCost.master_product_id, ProductCost.effective_from)
    ):
        history.setdefault(row.master_product_id, []).append(row)
    result: dict[tuple[int, date], EffectiveCost | None] = {}
    for request in requested:
        product_id, sales_date = request
        applicable = [
            row
            for row in history.get(product_id, ())
            if row.effective_from <= sales_date
        ]
        selected = applicable[-1] if applicable else None
        result[request] = (
            EffectiveCost(selected.cost, selected.effective_from) if selected else None
        )
    return result


def product_cost_history(session: Session, *, master_product_id: int) -> list[ProductCost]:
    return list(
        session.scalars(
            select(ProductCost)
            .where(ProductCost.master_product_id == master_product_id)
            .order_by(ProductCost.effective_from.desc(), ProductCost.id.desc())
        )
    )


def list_cost_overview(
    session: Session,
    *,
    reference_date: date,
    search: str = "",
    missing_only: bool = False,
) -> list[CostOverviewRow]:
    statement = (
        select(MasterProduct)
        .options(joinedload(MasterProduct.category))
        .where(MasterProduct.active.is_(True))
        .order_by(MasterProduct.display_name)
    )
    products = list(session.scalars(statement).unique())
    normalized_search = normalize_for_comparison(search)
    if normalized_search:
        products = [
            product
            for product in products
            if normalized_search in product.normalized_name
        ]
    effective = get_effective_costs(
        session, ((product.id, reference_date) for product in products)
    )
    rows = [
        CostOverviewRow(product, effective[(product.id, reference_date)])
        for product in products
    ]
    return [row for row in rows if row.effective_cost is None] if missing_only else rows


def cost_coverage(
    session: Session, *, date_from: date, date_to: date
) -> CostCoverage:
    sales = list(
        session.execute(
            select(SaleRecord.master_product_id, SaleRecord.sales_date).where(
                SaleRecord.sales_date >= date_from,
                SaleRecord.sales_date <= date_to,
            )
        )
    )
    resolved = get_effective_costs(session, sales)
    sold_products = {product_id for product_id, _ in sales}
    missing_products = {
        product_id
        for product_id in sold_products
        if any(
            resolved[(row_product_id, sales_date)] is None
            for row_product_id, sales_date in sales
            if row_product_id == product_id
        )
    }
    missing_sales = sum(
        1
        for product_id, sales_date in sales
        if resolved[(product_id, sales_date)] is None
    )
    return CostCoverage(
        products_with_cost=len(sold_products - missing_products),
        products_without_cost=len(missing_products),
        sale_records_without_cost=missing_sales,
    )


def import_cost_file(
    session: Session,
    *,
    file_path: str | Path,
    original_filename: str,
    effective_from: date,
    upload_directory: str | Path = DEFAULT_COST_UPLOAD_DIRECTORY,
    mapping: CostExcelMapping | None = None,
) -> CostImportResult:
    source = Path(file_path)
    sha256 = calculate_sha256(source)
    if session.scalar(
        select(CostImportBatch.id).where(
            CostImportBatch.effective_from == effective_from,
            CostImportBatch.sha256 == sha256,
        )
    ) is not None:
        raise DuplicateCostImportError(
            "Este mismo archivo de costos ya fue cargado para esa fecha de vigencia."
        )
    stored = store_original_file(
        source,
        original_filename=original_filename,
        upload_directory=upload_directory,
    )
    batch = CostImportBatch(
        effective_from=effective_from,
        source_filename=stored.original_filename,
        stored_filename=stored.stored_filename,
        sha256=sha256,
        status=PENDING,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    try:
        raw_rows = _read_cost_excel(source, mapping or CostExcelMapping())
    except CostExcelValidationError as exc:
        batch.status = FAILED
        batch.error_message = str(exc)
        session.commit()
        return _import_result(batch)

    products_by_name = {
        product.normalized_name: product
        for product in session.scalars(
            select(MasterProduct).where(MasterProduct.active.is_(True))
        )
    }
    for row_number, product_name, cost_raw in raw_rows:
        normalized_name = normalize_for_comparison(product_name or "")
        parsed_cost, error = _parse_cost(cost_raw)
        product = products_by_name.get(normalized_name)
        status = ROW_READY
        message = None
        if not normalized_name:
            status, message = ROW_INVALID, "El producto está vacío."
        elif error:
            status, message = ROW_INVALID, error
        elif product is None:
            status, message = ROW_PENDING, "No pudimos identificar este producto."
        elif parsed_cost == 0:
            status, message = ROW_WARNING, "El costo es cero; confirme que sea correcto."
        session.add(
            CostImportRow(
                batch=batch,
                source_row_number=row_number,
                original_product_name=product_name,
                normalized_product_name=normalized_name,
                cost_raw=_raw_text(cost_raw),
                parsed_cost=parsed_cost,
                master_product=product,
                status=status,
                message=message,
            )
        )
    batch.total_rows = len(raw_rows)
    session.commit()
    return finalize_cost_batch(session, batch_id=batch.id)


def list_pending_cost_rows(session: Session, *, batch_id: int) -> list[CostImportRow]:
    return list(
        session.scalars(
            select(CostImportRow)
            .where(
                CostImportRow.cost_import_batch_id == batch_id,
                CostImportRow.status == ROW_PENDING,
            )
            .order_by(CostImportRow.source_row_number)
        )
    )


def get_cost_import_batch(session: Session, *, batch_id: int) -> CostImportBatch | None:
    return session.scalar(
        select(CostImportBatch)
        .options(
            joinedload(CostImportBatch.rows).joinedload(CostImportRow.master_product)
        )
        .where(CostImportBatch.id == batch_id)
    )


def map_cost_row(
    session: Session, *, row_id: int, master_product_id: int
) -> CostImportResult:
    row = session.get(CostImportRow, row_id)
    product = session.get(MasterProduct, master_product_id)
    if row is None or row.status != ROW_PENDING:
        raise ValueError("La fila pendiente no está disponible.")
    if product is None or not product.active:
        raise ValueError("Selecciona un producto maestro válido.")
    row.master_product = product
    row.status = ROW_WARNING if row.parsed_cost == 0 else ROW_READY
    row.message = (
        "Producto relacionado manualmente. El costo cero requiere atención."
        if row.parsed_cost == 0
        else "Producto relacionado manualmente."
    )
    session.commit()
    return finalize_cost_batch(session, batch_id=row.cost_import_batch_id)


def skip_cost_row(session: Session, *, row_id: int) -> CostImportResult:
    row = session.get(CostImportRow, row_id)
    if row is None or row.status != ROW_PENDING:
        raise ValueError("La fila pendiente no está disponible.")
    row.status = ROW_SKIPPED
    row.message = "Producto omitido por decisión del usuario."
    session.commit()
    return finalize_cost_batch(session, batch_id=row.cost_import_batch_id)


def finalize_cost_batch(session: Session, *, batch_id: int) -> CostImportResult:
    batch = session.scalar(
        select(CostImportBatch)
        .options(joinedload(CostImportBatch.rows))
        .where(CostImportBatch.id == batch_id)
    )
    if batch is None:
        raise ValueError("La carga de costos no existe.")
    if batch.status == FAILED:
        return _import_result(batch)
    if any(row.status == ROW_PENDING for row in batch.rows):
        batch.status = WAITING_FOR_PRODUCT_MAPPING
        _refresh_batch_counts(batch)
        session.commit()
        return _import_result(batch)
    for row in batch.rows:
        if row.status not in {ROW_READY, ROW_WARNING}:
            continue
        if row.master_product_id is None or row.parsed_cost is None:
            row.status = ROW_INVALID
            row.message = "La fila no contiene un costo y producto válidos."
            continue
        existing = session.scalar(
            select(ProductCost).where(
                ProductCost.master_product_id == row.master_product_id,
                ProductCost.effective_from == batch.effective_from,
            )
        )
        if existing is not None:
            if existing.cost_import_batch_id == batch.id:
                row.status = ROW_IMPORTED
            else:
                row.status = ROW_CONFLICT
                row.message = "Ya existe un costo para este producto y fecha de vigencia."
            continue
        session.add(
            ProductCost(
                master_product_id=row.master_product_id,
                effective_from=batch.effective_from,
                cost=row.parsed_cost,
                source_filename=batch.source_filename,
                import_batch=batch,
                notes="Importado desde archivo de costos.",
            )
        )
        row.status = ROW_IMPORTED
    _refresh_batch_counts(batch)
    batch.status = COMPLETED_WITH_WARNINGS if batch.warning_rows else COMPLETED
    session.commit()
    return _import_result(batch)


def _read_cost_excel(path: Path, mapping: CostExcelMapping) -> list[tuple[int, str | None, object]]:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise CostExcelValidationError("El archivo debe tener extensión .xlsx o .xlsm.")
    try:
        frame = pd.read_excel(path, engine="openpyxl", dtype=object)
    except Exception as exc:
        raise CostExcelValidationError(
            "No fue posible abrir el archivo como un libro Excel válido."
        ) from exc
    normalized_columns = {
        normalize_for_comparison(str(column)): column for column in frame.columns
    }
    product_column = next(
        (normalized_columns.get(normalize_for_comparison(alias)) for alias in mapping.product_aliases if normalized_columns.get(normalize_for_comparison(alias)) is not None),
        None,
    )
    cost_column = next(
        (normalized_columns.get(normalize_for_comparison(alias)) for alias in mapping.cost_aliases if normalized_columns.get(normalize_for_comparison(alias)) is not None),
        None,
    )
    if product_column is None or cost_column is None:
        raise CostExcelValidationError(
            "El archivo debe incluir las columnas Producto y Costo."
        )
    if frame.empty:
        raise CostExcelValidationError("El archivo Excel no contiene filas de costos.")
    return [
        (index + 2, _optional_text(row[product_column]), row[cost_column])
        for index, (_, row) in enumerate(frame.iterrows())
    ]


def _parse_cost(value: object) -> tuple[Decimal | None, str | None]:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None, "El costo está vacío."
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None, "El costo está vacío."
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None, "El costo no contiene un número válido."
    if not number.is_finite():
        return None, "El costo no contiene un número válido."
    if number < 0:
        return None, "El costo negativo no se importó."
    return number, None


def _optional_text(value: object) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _raw_text(value: object) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return str(value)


def _refresh_batch_counts(batch: CostImportBatch) -> None:
    batch.imported_rows = sum(row.status == ROW_IMPORTED for row in batch.rows)
    batch.warning_rows = sum(
        row.status in {ROW_WARNING, ROW_INVALID, ROW_CONFLICT, ROW_SKIPPED}
        or bool(row.message and row.status == ROW_IMPORTED)
        for row in batch.rows
    )


def _import_result(batch: CostImportBatch) -> CostImportResult:
    return CostImportResult(
        batch_id=batch.id,
        status=batch.status,
        total_rows=batch.total_rows,
        imported_rows=batch.imported_rows,
        pending_rows=sum(row.status == ROW_PENDING for row in batch.rows),
        warning_rows=batch.warning_rows,
    )
