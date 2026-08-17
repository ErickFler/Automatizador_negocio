from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.db.database import Base, seed_branches
from app.db.models import (
    Branch,
    ImportBatch,
    ProcessingIssue,
    RawSaleRecord,
    SaleRecord,
)
from app.services.product_catalog import (
    create_branch_product,
    create_master_product,
    get_or_create_category,
)
from app.services.raw_import_service import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    WAITING_FOR_PRODUCT_MAPPING,
)
from app.services.sales_processing_service import process_normalized_sales_batch


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        seed_branches(database_session)
        yield database_session


def _batch_with_row(
    session: Session,
    *,
    total_units: str | None = "26",
    sale_price: str | None = "49",
    cost: str | None = "20",
    amount: str | None = "49",
    sales_amount: str | None = "49",
    mapped: bool = True,
) -> tuple[ImportBatch, RawSaleRecord, object | None, object | None]:
    branch = session.scalar(select(Branch).where(Branch.name == "Unidad"))
    assert branch is not None
    batch = ImportBatch(
        sales_date=date(2026, 8, 16),
        branch=branch,
        source_filename="unidad.xlsx",
        stored_filename="stage4_unidad.xlsx",
        sha256="4" * 64,
        total_rows=1,
        status=WAITING_FOR_PRODUCT_MAPPING,
    )
    row = RawSaleRecord(
        import_batch=batch,
        source_row_number=2,
        original_category_name="POLLOS",
        external_product_id="500",
        original_product_name="PURE FAMILIAR",
        quantity_raw=total_units,
        sale_price_raw=sale_price,
        cost_raw=cost,
        amount_raw=amount,
        total_amount_raw=sales_amount,
    )
    session.add_all([batch, row])
    branch_product = None
    master = None
    if mapped:
        category, _ = get_or_create_category(session, display_name="Pollos")
        master = create_master_product(
            session, display_name="Puré familiar", category=category
        )
        branch_product = create_branch_product(
            session,
            branch=branch,
            external_product_id="500",
            original_product_name="PURE FAMILIAR",
            original_category_name="POLLOS",
            master_product=master,
        )
    session.commit()
    return batch, row, branch_product, master


def _issue_types(session: Session, batch_id: int) -> list[str]:
    return session.scalars(
        select(ProcessingIssue.issue_type).where(
            ProcessingIssue.import_batch_id == batch_id
        )
    ).all()


def test_homologated_unidad_sale_has_complete_traceability(session: Session) -> None:
    batch, raw, branch_product, master = _batch_with_row(session)

    result = process_normalized_sales_batch(session, batch_id=batch.id)
    sale = session.scalar(select(SaleRecord))

    assert result.status == COMPLETED
    assert result.normalized_rows == 1
    assert result.skipped_rows == 0
    assert sale is not None
    assert sale.total_units == Decimal("26")
    assert sale.individual_sale_units == Decimal("1")
    assert sale.combo_units == Decimal("25")
    assert sale.sales_amount == Decimal("49")
    assert sale.branch_id == batch.branch_id
    assert sale.branch_product_id == branch_product.id
    assert sale.master_product_id == master.id
    assert sale.raw_sale_record_id == raw.id
    assert sale.import_batch_id == batch.id
    assert raw.quantity_raw == "26"
    assert raw.total_amount_raw == "49"


@pytest.mark.parametrize(
    ("overrides", "expected_issue"),
    [
        ({"total_units": "ERROR"}, "INVALID_QUANTITY"),
        ({"sale_price": "ERROR"}, "INVALID_SALE_PRICE"),
        ({"sales_amount": "ERROR"}, "INVALID_TOTAL_AMOUNT"),
    ],
)
def test_invalid_required_value_blocks_only_the_normalized_sale(
    session: Session, overrides: dict[str, str], expected_issue: str
) -> None:
    batch, _, _, _ = _batch_with_row(session, **overrides)

    result = process_normalized_sales_batch(session, batch_id=batch.id)

    assert result.status == COMPLETED_WITH_WARNINGS
    assert result.normalized_rows == 0
    assert result.skipped_rows == 1
    assert session.scalar(select(func.count(SaleRecord.id))) == 0
    assert expected_issue in _issue_types(session, batch.id)


def test_zero_sale_price_blocks_division_and_sale(session: Session) -> None:
    batch, raw, _, _ = _batch_with_row(
        session, total_units="26", sale_price="0", sales_amount="0"
    )

    result = process_normalized_sales_batch(session, batch_id=batch.id)

    assert result.status == COMPLETED_WITH_WARNINGS
    assert "ZERO_SALE_PRICE" in _issue_types(session, batch.id)
    assert session.scalar(select(func.count(SaleRecord.id))) == 0
    assert raw.quantity_raw == "26"
    assert raw.sale_price_raw == "0"
    assert raw.total_amount_raw == "0"
    assert not any(
        "unclassified" in column.name or "pending" in column.name
        for column in SaleRecord.__table__.columns
    )


def test_zero_sales_amount_is_valid_and_preserves_combo_units(session: Session) -> None:
    batch, _, _, _ = _batch_with_row(session, sales_amount="0")

    result = process_normalized_sales_batch(session, batch_id=batch.id)
    sale = session.scalar(select(SaleRecord))

    assert result.status == COMPLETED
    assert sale.individual_sale_units == Decimal("0")
    assert sale.combo_units == Decimal("26")


def test_invalid_cost_and_original_amount_are_nonblocking(session: Session) -> None:
    batch, _, _, _ = _batch_with_row(session, cost="ERROR", amount="NO DISPONIBLE")

    result = process_normalized_sales_batch(session, batch_id=batch.id)
    sale = session.scalar(select(SaleRecord))

    assert result.status == COMPLETED_WITH_WARNINGS
    assert sale is not None
    assert sale.original_cost is None
    assert sale.original_amount is None
    assert {"INVALID_ORIGINAL_COST", "INVALID_ORIGINAL_AMOUNT"}.issubset(
        set(_issue_types(session, batch.id))
    )


@pytest.mark.parametrize(
    ("values", "expected_issues", "individual", "combo"),
    [
        (
            {"total_units": "10", "sale_price": "30", "sales_amount": "100"},
            {"NON_INTEGER_INDIVIDUAL_SALE_UNITS"},
            Decimal("100") / Decimal("30"),
            Decimal("10") - (Decimal("100") / Decimal("30")),
        ),
        (
            {"total_units": "2", "sale_price": "10", "sales_amount": "30"},
            {"INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS", "NEGATIVE_COMBO_UNITS"},
            Decimal("3"),
            Decimal("-1"),
        ),
        (
            {"total_units": "-2", "sale_price": "43", "sales_amount": "43"},
            {"NEGATIVE_TOTAL_UNITS", "INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS"},
            Decimal("1"),
            Decimal("-3"),
        ),
        (
            {"total_units": "2", "sale_price": "-43", "sales_amount": "43"},
            {"NEGATIVE_SALE_PRICE", "NEGATIVE_INDIVIDUAL_SALE_UNITS"},
            Decimal("-1"),
            Decimal("3"),
        ),
        (
            {"total_units": "2", "sale_price": "43", "sales_amount": "-43"},
            {"NEGATIVE_SALES_AMOUNT", "NEGATIVE_INDIVIDUAL_SALE_UNITS"},
            Decimal("-1"),
            Decimal("3"),
        ),
    ],
)
def test_auditable_anomalies_are_preserved_without_correction(
    session: Session,
    values: dict[str, str],
    expected_issues: set[str],
    individual: Decimal,
    combo: Decimal,
) -> None:
    batch, _, _, _ = _batch_with_row(session, **values)

    result = process_normalized_sales_batch(session, batch_id=batch.id)
    sale = session.scalar(select(SaleRecord))

    assert result.status == COMPLETED_WITH_WARNINGS
    assert sale is not None
    assert sale.individual_sale_units == pytest.approx(
        individual, rel=Decimal("1e-11")
    )
    assert sale.combo_units == pytest.approx(combo, rel=Decimal("1e-11"))
    assert expected_issues.issubset(set(_issue_types(session, batch.id)))


def test_unmapped_product_keeps_batch_waiting_and_creates_no_sale(
    session: Session,
) -> None:
    batch, _, _, _ = _batch_with_row(session, mapped=False)

    result = process_normalized_sales_batch(session, batch_id=batch.id)

    assert result.status == WAITING_FOR_PRODUCT_MAPPING
    assert result.normalized_rows == 0
    assert result.skipped_rows == 1
    assert "UNMAPPED_PRODUCT" in _issue_types(session, batch.id)
    assert session.scalar(select(func.count(SaleRecord.id))) == 0


def test_reprocessing_does_not_duplicate_sale_or_stage4_issues(session: Session) -> None:
    batch, _, _, _ = _batch_with_row(
        session, total_units="2", sale_price="10", sales_amount="30"
    )

    process_normalized_sales_batch(session, batch_id=batch.id)
    first_issue_count = session.scalar(select(func.count(ProcessingIssue.id)))
    process_normalized_sales_batch(session, batch_id=batch.id)

    assert session.scalar(select(func.count(SaleRecord.id))) == 1
    assert session.scalar(select(func.count(ProcessingIssue.id))) == first_issue_count


def test_database_has_unique_constraint_for_raw_sale_traceability(
    session: Session,
) -> None:
    indexes = inspect(session.get_bind()).get_indexes("sale_records")

    assert any(
        index["unique"] and index["column_names"] == ["raw_sale_record_id"]
        for index in indexes
    )
