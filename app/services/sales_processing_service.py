from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BranchProduct,
    ImportBatch,
    ProcessingIssue,
    RawSaleRecord,
    SaleRecord,
)
from app.domain.calculations import calculate_units
from app.services.numeric_parser import DecimalParseResult, parse_decimal
from app.services.raw_import_service import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    ERROR,
    FAILED,
    WAITING_FOR_PRODUCT_MAPPING,
    WARNING,
)

INVALID_QUANTITY = "INVALID_QUANTITY"
INVALID_SALE_PRICE = "INVALID_SALE_PRICE"
INVALID_TOTAL_AMOUNT = "INVALID_TOTAL_AMOUNT"
INVALID_ORIGINAL_COST = "INVALID_ORIGINAL_COST"
INVALID_ORIGINAL_AMOUNT = "INVALID_ORIGINAL_AMOUNT"
UNMAPPED_PRODUCT = "UNMAPPED_PRODUCT"

STAGE_4_ISSUE_TYPES = frozenset(
    {
        INVALID_QUANTITY,
        INVALID_SALE_PRICE,
        INVALID_TOTAL_AMOUNT,
        INVALID_ORIGINAL_COST,
        INVALID_ORIGINAL_AMOUNT,
        UNMAPPED_PRODUCT,
        "ZERO_SALE_PRICE",
        "NON_INTEGER_INDIVIDUAL_SALE_UNITS",
        "INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS",
        "NEGATIVE_TOTAL_UNITS",
        "NEGATIVE_SALE_PRICE",
        "NEGATIVE_SALES_AMOUNT",
        "NEGATIVE_INDIVIDUAL_SALE_UNITS",
        "NEGATIVE_COMBO_UNITS",
    }
)


@dataclass(frozen=True, slots=True)
class BatchProcessingResult:
    batch_id: int
    status: str
    total_rows: int
    normalized_rows: int
    issue_rows: int
    skipped_rows: int


@dataclass(frozen=True, slots=True)
class BatchSalesSummary:
    total_units: Decimal
    individual_sale_units: Decimal
    combo_units: Decimal
    sales_amount: Decimal


def process_normalized_sales_batch(
    session: Session, *, batch_id: int
) -> BatchProcessingResult:
    """Normaliza un lote homologado de forma transaccional e idempotente."""
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise ValueError("La carga solicitada no existe.")
    if batch.status == FAILED:
        raise ValueError("Una carga fallida estructuralmente no puede normalizarse.")

    raw_rows = session.scalars(
        select(RawSaleRecord)
        .where(RawSaleRecord.import_batch_id == batch_id)
        .order_by(RawSaleRecord.source_row_number)
    ).all()
    branch_products = {
        item.external_product_id: item
        for item in session.scalars(
            select(BranchProduct).where(BranchProduct.branch_id == batch.branch_id)
        ).all()
    }

    session.execute(
        delete(ProcessingIssue).where(
            ProcessingIssue.import_batch_id == batch_id,
            ProcessingIssue.issue_type.in_(STAGE_4_ISSUE_TYPES),
        )
    )
    session.flush()

    unmapped_rows = [
        row
        for row in raw_rows
        if (row.external_product_id or "").strip() not in branch_products
    ]
    if unmapped_rows:
        session.execute(
            delete(SaleRecord).where(SaleRecord.import_batch_id == batch_id)
        )
        for row in unmapped_rows:
            _add_issue(
                session,
                batch=batch,
                row=row,
                issue_type=UNMAPPED_PRODUCT,
                severity=ERROR,
                message=(
                    "El producto local no está homologado; confirma el producto antes "
                    "de normalizar la carga."
                ),
            )
        batch.normalized_rows = 0
        batch.skipped_rows = batch.total_rows
        batch.issue_rows = _count_issue_rows(session, batch.id)
        batch.status = WAITING_FOR_PRODUCT_MAPPING
        session.commit()
        return _result(batch)

    existing_sales = {
        sale.raw_sale_record_id: sale
        for sale in session.scalars(
            select(SaleRecord).where(SaleRecord.import_batch_id == batch_id)
        ).all()
    }
    normalized_rows = 0
    skipped_rows = 0

    for row in raw_rows:
        branch_product = branch_products[(row.external_product_id or "").strip()]
        total_units = parse_decimal(row.quantity_raw)
        sale_price = parse_decimal(row.sale_price_raw)
        sales_amount = parse_decimal(row.total_amount_raw)
        original_cost = parse_decimal(row.cost_raw)
        original_amount = parse_decimal(row.amount_raw)

        blocking = False
        blocking |= _record_parse_error(
            session,
            batch,
            row,
            total_units,
            INVALID_QUANTITY,
            "Cantidad",
            blocking=True,
        )
        blocking |= _record_parse_error(
            session,
            batch,
            row,
            sale_price,
            INVALID_SALE_PRICE,
            "PrecioVenta",
            blocking=True,
        )
        blocking |= _record_parse_error(
            session,
            batch,
            row,
            sales_amount,
            INVALID_TOTAL_AMOUNT,
            "ImporteTotal",
            blocking=True,
        )
        _record_parse_error(
            session,
            batch,
            row,
            original_cost,
            INVALID_ORIGINAL_COST,
            "Costo",
            blocking=False,
        )
        _record_parse_error(
            session,
            batch,
            row,
            original_amount,
            INVALID_ORIGINAL_AMOUNT,
            "Importe",
            blocking=False,
        )

        if sale_price.value == 0:
            blocking = True
            _add_issue(
                session,
                batch=batch,
                row=row,
                issue_type="ZERO_SALE_PRICE",
                severity=ERROR,
                message="PrecioVenta es cero; no es posible calcular unidades cobradas.",
            )

        if blocking:
            existing = existing_sales.get(row.id)
            if existing is not None:
                session.delete(existing)
            skipped_rows += 1
            continue

        calculation = calculate_units(
            total_units=total_units.value,
            sale_price=sale_price.value,
            sales_amount=sales_amount.value,
        )
        if (
            calculation.individual_sale_units is None
            or calculation.combo_units is None
        ):
            raise RuntimeError("Los valores validados no produjeron un cálculo completo.")
        for warning in calculation.warnings:
            _add_issue(
                session,
                batch=batch,
                row=row,
                issue_type=warning.warning_type,
                severity=WARNING,
                message=warning.description,
            )

        sale = existing_sales.get(row.id)
        if sale is None:
            sale = SaleRecord(raw_sale_record_id=row.id)
            session.add(sale)
        sale.import_batch_id = batch.id
        sale.branch_id = batch.branch_id
        sale.branch_product_id = branch_product.id
        sale.master_product_id = branch_product.master_product_id
        sale.sales_date = batch.sales_date
        sale.total_units = total_units.value
        sale.sale_price = sale_price.value
        sale.original_cost = original_cost.value
        sale.original_amount = original_amount.value
        sale.sales_amount = sales_amount.value
        sale.individual_sale_units = calculation.individual_sale_units
        sale.combo_units = calculation.combo_units
        sale.source_row_number = row.source_row_number
        normalized_rows += 1

    session.flush()
    batch.normalized_rows = normalized_rows
    batch.skipped_rows = skipped_rows
    batch.issue_rows = _count_issue_rows(session, batch.id)
    batch.status = COMPLETED_WITH_WARNINGS if batch.issue_rows else COMPLETED
    batch.error_message = None
    session.commit()
    return _result(batch)


def get_batch_sales_summary(session: Session, *, batch_id: int) -> BatchSalesSummary:
    rows = session.execute(
        select(
            SaleRecord.total_units,
            SaleRecord.individual_sale_units,
            SaleRecord.combo_units,
            SaleRecord.sales_amount,
        ).where(SaleRecord.import_batch_id == batch_id)
    ).all()
    return BatchSalesSummary(
        total_units=sum((row.total_units for row in rows), start=Decimal(0)),
        individual_sale_units=sum(
            (row.individual_sale_units for row in rows), start=Decimal(0)
        ),
        combo_units=sum((row.combo_units for row in rows), start=Decimal(0)),
        sales_amount=sum((row.sales_amount for row in rows), start=Decimal(0)),
    )


def _record_parse_error(
    session: Session,
    batch: ImportBatch,
    row: RawSaleRecord,
    parsed: DecimalParseResult,
    issue_type: str,
    field_label: str,
    *,
    blocking: bool,
) -> bool:
    if parsed.is_valid:
        return False
    raw_by_type = {
        INVALID_QUANTITY: row.quantity_raw,
        INVALID_SALE_PRICE: row.sale_price_raw,
        INVALID_TOTAL_AMOUNT: row.total_amount_raw,
        INVALID_ORIGINAL_COST: row.cost_raw,
        INVALID_ORIGINAL_AMOUNT: row.amount_raw,
    }
    raw_value = raw_by_type[issue_type]
    _add_issue(
        session,
        batch=batch,
        row=row,
        issue_type=issue_type,
        severity=ERROR if blocking else WARNING,
        message=(
            f"{field_label} no contiene un número válido "
            f"(valor recibido: {raw_value!r})."
        ),
    )
    return blocking


def _add_issue(
    session: Session,
    *,
    batch: ImportBatch,
    row: RawSaleRecord,
    issue_type: str,
    severity: str,
    message: str,
) -> None:
    session.add(
        ProcessingIssue(
            import_batch=batch,
            raw_sale_record=row,
            source_row_number=row.source_row_number,
            issue_type=issue_type,
            severity=severity,
            message=message,
        )
    )


def _count_issue_rows(session: Session, batch_id: int) -> int:
    return int(
        session.scalar(
            select(func.count(func.distinct(ProcessingIssue.source_row_number))).where(
                ProcessingIssue.import_batch_id == batch_id,
                ProcessingIssue.source_row_number.is_not(None),
            )
        )
        or 0
    )


def _result(batch: ImportBatch) -> BatchProcessingResult:
    return BatchProcessingResult(
        batch_id=batch.id,
        status=batch.status,
        total_rows=batch.total_rows,
        normalized_rows=batch.normalized_rows,
        issue_rows=batch.issue_rows,
        skipped_rows=batch.skipped_rows,
    )
