from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Category, ImportBatch, Product, SaleRecord
from app.domain.normalization import PENDING_CATEGORY_NAME
from app.services.consolidation_service import (
    ConsolidationFilters,
    consolidate_sales,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session


def _add_batch(
    session: Session,
    *,
    batch_number: int,
    sales_date: date,
    branch: str,
    status: str = "COMPLETED",
) -> ImportBatch:
    batch = ImportBatch(
        sales_date=sales_date,
        branch=branch,
        source_filename=f"ventas_{batch_number}.xlsx",
        stored_filename=f"uuid_{batch_number}_ventas.xlsx",
        file_sha256=str(batch_number).zfill(64),
        status=status,
    )
    session.add(batch)
    session.flush()
    return batch


def _add_product(
    session: Session,
    *,
    external_id: str,
    name: str,
    category_name: str | None,
) -> Product:
    category = None
    if category_name is not None:
        category = Category(
            name=category_name,
            normalized_name=category_name.casefold(),
        )
        session.add(category)
    product = Product(
        external_product_id=external_id,
        original_name=name,
        normalized_name=name.casefold(),
        category=category,
    )
    session.add(product)
    session.flush()
    return product


def _add_sale(
    session: Session,
    *,
    batch: ImportBatch,
    product: Product,
    row_number: int,
    quantity: Decimal | None,
    real_units: Decimal | None,
    combo_units: Decimal | None,
    total_amount: Decimal | None,
) -> None:
    session.add(
        SaleRecord(
            batch=batch,
            product=product,
            sales_date=batch.sales_date,
            branch=batch.branch,
            quantity=quantity,
            sale_price=None,
            unit_cost=None,
            cost_amount=None,
            total_amount=total_amount,
            real_units=real_units,
            combo_units=combo_units,
            source_row_number=row_number,
        )
    )


def test_same_dimensions_are_grouped_and_summed(session: Session) -> None:
    batch = _add_batch(
        session,
        batch_number=1,
        sales_date=date(2026, 8, 8),
        branch="Norte",
    )
    product = _add_product(
        session,
        external_id="139",
        name="REFRESCO PEPSI 2 LTS",
        category_name="BEBIDAS",
    )
    _add_sale(
        session,
        batch=batch,
        product=product,
        row_number=2,
        quantity=Decimal("2"),
        real_units=Decimal("2"),
        combo_units=Decimal("0"),
        total_amount=Decimal("86"),
    )
    _add_sale(
        session,
        batch=batch,
        product=product,
        row_number=3,
        quantity=Decimal("26"),
        real_units=Decimal("1"),
        combo_units=Decimal("25"),
        total_amount=Decimal("49"),
    )
    session.commit()

    result = consolidate_sales(session)

    assert len(result) == 1
    assert result[0].sales_date == date(2026, 8, 8)
    assert result[0].branch == "Norte"
    assert result[0].category == "BEBIDAS"
    assert result[0].external_product_id == "139"
    assert result[0].product == "REFRESCO PEPSI 2 LTS"
    assert result[0].registered_quantity == Decimal("28.000000")
    assert result[0].real_units == Decimal("3.000000")
    assert result[0].combo_units == Decimal("25.000000")
    assert result[0].total_amount == Decimal("135.000000")


def test_date_branch_category_and_product_remain_separate_and_ordered(
    session: Session,
) -> None:
    north = _add_batch(
        session,
        batch_number=1,
        sales_date=date(2026, 8, 8),
        branch="Norte",
    )
    south = _add_batch(
        session,
        batch_number=2,
        sales_date=date(2026, 8, 8),
        branch="Sur",
    )
    food = _add_product(
        session, external_id="140", name="PAPA", category_name="ALIMENTOS"
    )
    beverage = _add_product(
        session, external_id="139", name="REFRESCO", category_name="BEBIDAS"
    )
    for batch, product, row in (
        (north, beverage, 2),
        (north, food, 3),
        (south, beverage, 2),
    ):
        _add_sale(
            session,
            batch=batch,
            product=product,
            row_number=row,
            quantity=Decimal("1"),
            real_units=Decimal("1"),
            combo_units=Decimal("0"),
            total_amount=Decimal("10"),
        )
    session.commit()

    result = consolidate_sales(session)

    assert [(row.branch, row.category, row.product) for row in result] == [
        ("Norte", "ALIMENTOS", "PAPA"),
        ("Norte", "BEBIDAS", "REFRESCO"),
        ("Sur", "BEBIDAS", "REFRESCO"),
    ]


def test_failed_batches_are_excluded(session: Session) -> None:
    completed = _add_batch(
        session,
        batch_number=1,
        sales_date=date(2026, 8, 8),
        branch="Norte",
    )
    failed = _add_batch(
        session,
        batch_number=2,
        sales_date=date(2026, 8, 8),
        branch="Norte",
        status="FAILED",
    )
    product = _add_product(
        session, external_id="139", name="REFRESCO", category_name="BEBIDAS"
    )
    for batch, quantity in ((completed, "1"), (failed, "100")):
        _add_sale(
            session,
            batch=batch,
            product=product,
            row_number=2,
            quantity=Decimal(quantity),
            real_units=Decimal(quantity),
            combo_units=Decimal("0"),
            total_amount=Decimal(quantity),
        )
    session.commit()

    result = consolidate_sales(session)

    assert len(result) == 1
    assert result[0].registered_quantity == Decimal("1.000000")


def test_unknown_totals_remain_null_and_missing_category_uses_pending_label(
    session: Session,
) -> None:
    batch = _add_batch(
        session,
        batch_number=1,
        sales_date=date(2026, 8, 8),
        branch="Norte",
        status="COMPLETED_WITH_WARNINGS",
    )
    product = _add_product(
        session, external_id="200", name="SIN CATEGORÍA", category_name=None
    )
    _add_sale(
        session,
        batch=batch,
        product=product,
        row_number=2,
        quantity=None,
        real_units=None,
        combo_units=None,
        total_amount=None,
    )
    session.commit()

    result = consolidate_sales(session)

    assert result[0].category == PENDING_CATEGORY_NAME
    assert result[0].registered_quantity is None
    assert result[0].real_units is None
    assert result[0].combo_units is None
    assert result[0].total_amount is None


def test_filters_limit_historical_results(session: Session) -> None:
    first = _add_batch(
        session,
        batch_number=1,
        sales_date=date(2026, 8, 8),
        branch="Norte",
    )
    second = _add_batch(
        session,
        batch_number=2,
        sales_date=date(2026, 8, 9),
        branch="Sur",
    )
    product = _add_product(
        session, external_id="139", name="REFRESCO", category_name="BEBIDAS"
    )
    for batch in (first, second):
        _add_sale(
            session,
            batch=batch,
            product=product,
            row_number=2,
            quantity=Decimal("1"),
            real_units=Decimal("1"),
            combo_units=Decimal("0"),
            total_amount=Decimal("43"),
        )
    session.commit()

    result = consolidate_sales(
        session,
        ConsolidationFilters(
            sales_date_from=date(2026, 8, 9),
            sales_date_to=date(2026, 8, 9),
            branches=(" Sur ",),
            product_ids=(product.id,),
        ),
    )

    assert len(result) == 1
    assert result[0].sales_date == date(2026, 8, 9)
    assert result[0].branch == "Sur"


def test_invalid_date_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="sales_date_from"):
        ConsolidationFilters(
            sales_date_from=date(2026, 8, 9),
            sales_date_to=date(2026, 8, 8),
        )

