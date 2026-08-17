from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db.database import Base, seed_branches
from app.db.models import (
    Branch,
    BranchProduct,
    Category,
    ImportBatch,
    MasterProduct,
    ProcessingIssue,
    RawSaleRecord,
    SaleRecord,
)
from app.domain.normalization import normalize_for_comparison
from app.services.aggregation_service import (
    ALLOWED_METRICS,
    AggregationFilters,
    aggregate_by_branch,
    aggregate_by_category,
    aggregate_by_product,
    aggregate_daily,
    aggregate_totals,
    iso_week_bounds,
    iso_year_and_week,
    metric_value,
    monthly_series,
    price_history,
    summarize_issues,
    weekly_daily_series,
    weekly_series,
)


@pytest.fixture
def historical_data() -> dict[str, object]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)
    branches = {
        branch.name: branch
        for branch in session.scalars(select(Branch).order_by(Branch.id))
    }
    beverages = _category(session, "Bebidas")
    food = _category(session, "Alimentos")
    products = {
        "Agua": _master(session, "Agua", beverages),
        "Pollo": _master(session, "Pollo", food),
        "Yogur": _master(session, "Yogur", food),
        "Zeta": _master(session, "Zeta", beverages),
    }
    branch_products = {
        ("Loma Linda", "Agua"): _branch_product(
            session, branches["Loma Linda"], products["Agua"], "344"
        ),
        ("Alianza", "Agua"): _branch_product(
            session, branches["Alianza"], products["Agua"], "347"
        ),
        ("Unidad", "Agua"): _branch_product(
            session, branches["Unidad"], products["Agua"], "999"
        ),
        ("Loma Linda", "Pollo"): _branch_product(
            session, branches["Loma Linda"], products["Pollo"], "500"
        ),
        ("Alianza", "Pollo"): _branch_product(
            session, branches["Alianza"], products["Pollo"], "501"
        ),
        ("Loma Linda", "Yogur"): _branch_product(
            session, branches["Loma Linda"], products["Yogur"], "600"
        ),
        ("Loma Linda", "Zeta"): _branch_product(
            session, branches["Loma Linda"], products["Zeta"], "700"
        ),
    }
    counter = 1
    for values in (
        (date(2025, 12, 29), "Loma Linda", "Agua", "10", "8", "2", "80", "10"),
        (date(2026, 1, 1), "Alianza", "Agua", "5", "3", "2", "30", "10"),
        (date(2026, 1, 1), "Loma Linda", "Pollo", "4", "4", "0", "100", "25"),
        (date(2026, 1, 3), "Loma Linda", "Yogur", "4", "4", "0", "100", "25"),
        (date(2026, 1, 3), "Loma Linda", "Zeta", "4", "4", "0", "100", "25"),
        (date(2026, 2, 2), "Loma Linda", "Agua", "2", "2", "0", "24", "12"),
        (date(2026, 2, 2), "Alianza", "Pollo", "6", "2", "4", "60", "30"),
    ):
        counter = _sale(session, counter, branches, products, branch_products, *values)

    issue_batch = _batch(
        session, counter, date(2026, 1, 2), branches["Unidad"], status="COMPLETED_WITH_WARNINGS"
    )
    issue_raw = RawSaleRecord(
        import_batch=issue_batch,
        source_row_number=2,
        external_product_id="999",
        original_product_name="PRECIO CERO",
        original_category_name="BEBIDAS",
        quantity_raw="3",
        sale_price_raw="0",
        total_amount_raw="0",
    )
    session.add(issue_raw)
    session.flush()
    session.add(
        ProcessingIssue(
            import_batch=issue_batch,
            raw_sale_record=issue_raw,
            source_row_number=2,
            issue_type="ZERO_SALE_PRICE",
            severity="ERROR",
            message="PrecioVenta es cero.",
        )
    )
    issue_batch.total_rows = 1
    issue_batch.normalized_rows = 0
    issue_batch.issue_rows = 1
    issue_batch.skipped_rows = 1
    session.commit()

    yield {
        "session": session,
        "branches": branches,
        "categories": {"Bebidas": beverages, "Alimentos": food},
        "products": products,
    }
    session.close()


def _category(session: Session, name: str) -> Category:
    category = Category(
        display_name=name, normalized_name=normalize_for_comparison(name)
    )
    session.add(category)
    session.flush()
    return category


def _master(session: Session, name: str, category: Category) -> MasterProduct:
    product = MasterProduct(
        display_name=name,
        normalized_name=normalize_for_comparison(name),
        category=category,
    )
    session.add(product)
    session.flush()
    return product


def _branch_product(
    session: Session,
    branch: Branch,
    product: MasterProduct,
    external_id: str,
) -> BranchProduct:
    mapping = BranchProduct(
        branch=branch,
        master_product=product,
        external_product_id=external_id,
        original_product_name=product.display_name.upper(),
        normalized_product_name=product.normalized_name,
        original_category_name=product.category.display_name.upper(),
    )
    session.add(mapping)
    session.flush()
    return mapping


def _batch(
    session: Session,
    number: int,
    sales_date: date,
    branch: Branch,
    *,
    status: str = "COMPLETED",
) -> ImportBatch:
    batch = ImportBatch(
        sales_date=sales_date,
        branch=branch,
        source_filename=f"ventas_{number}.xlsx",
        stored_filename=f"stage5_{number}.xlsx",
        sha256=f"{number:064d}",
        status=status,
        total_rows=1,
        normalized_rows=1,
    )
    session.add(batch)
    session.flush()
    return batch


def _sale(
    session: Session,
    number: int,
    branches: dict[str, Branch],
    products: dict[str, MasterProduct],
    mappings: dict[tuple[str, str], BranchProduct],
    sales_date: date,
    branch_name: str,
    product_name: str,
    total_units: str,
    individual_units: str,
    combo_units: str,
    sales_amount: str,
    sale_price: str,
) -> int:
    branch = branches[branch_name]
    product = products[product_name]
    mapping = mappings[(branch_name, product_name)]
    batch = _batch(session, number, sales_date, branch)
    raw = RawSaleRecord(
        import_batch=batch,
        source_row_number=2,
        external_product_id=mapping.external_product_id,
        original_product_name=mapping.original_product_name,
        quantity_raw=total_units,
        sale_price_raw=sale_price,
        total_amount_raw=sales_amount,
    )
    session.add(raw)
    session.flush()
    session.add(
        SaleRecord(
            batch=batch,
            raw_sale_record=raw,
            branch=branch,
            branch_product=mapping,
            master_product=product,
            sales_date=sales_date,
            total_units=Decimal(total_units),
            sale_price=Decimal(sale_price),
            sales_amount=Decimal(sales_amount),
            individual_sale_units=Decimal(individual_units),
            combo_units=Decimal(combo_units),
            source_row_number=2,
        )
    )
    return number + 1


def _context(data: dict[str, object]) -> tuple[Session, dict, dict, dict]:
    return (
        data["session"],
        data["branches"],
        data["categories"],
        data["products"],
    )


def test_official_metrics_sum_with_decimal(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    result = aggregate_totals(session)

    assert result.total_units == Decimal("35")
    assert result.individual_sale_units == Decimal("27")
    assert result.combo_units == Decimal("8")
    assert result.sales_amount == Decimal("494")
    assert all(isinstance(metric_value(result, metric), Decimal) for metric in ALLOWED_METRICS)


def test_exact_date_and_inclusive_range_filters(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    exact = aggregate_totals(
        session, AggregationFilters(date_from=date(2026, 1, 1), date_to=date(2026, 1, 1))
    )
    ranged = aggregate_totals(
        session, AggregationFilters(date_from=date(2025, 12, 29), date_to=date(2026, 1, 1))
    )

    assert exact == type(exact)(Decimal("9"), Decimal("7"), Decimal("2"), Decimal("130"))
    assert ranged.sales_amount == Decimal("210")


def test_branch_product_category_and_combined_filters(historical_data: dict[str, object]) -> None:
    session, branches, categories, products = _context(historical_data)

    by_branch = aggregate_totals(
        session, AggregationFilters(branch_id=branches["Alianza"].id)
    )
    by_product = aggregate_totals(
        session, AggregationFilters(master_product_id=products["Agua"].id)
    )
    by_category = aggregate_totals(
        session, AggregationFilters(category_id=categories["Alimentos"].id)
    )
    combined = aggregate_totals(
        session,
        AggregationFilters(
            date_from=date(2026, 2, 1),
            branch_id=branches["Alianza"].id,
            category_id=categories["Alimentos"].id,
        ),
    )

    assert by_branch.total_units == Decimal("11")
    assert by_product.total_units == Decimal("17")
    assert by_category.sales_amount == Decimal("260")
    assert combined == type(combined)(Decimal("6"), Decimal("2"), Decimal("4"), Decimal("60"))


def test_daily_aggregation_with_optional_branch_dimension(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    daily = aggregate_daily(session)
    daily_branches = aggregate_daily(
        session,
        AggregationFilters(date_from=date(2026, 1, 1), date_to=date(2026, 1, 1)),
        by_branch=True,
    )

    assert [row.sales_date for row in daily] == [
        date(2025, 12, 29), date(2026, 1, 1), date(2026, 1, 3), date(2026, 2, 2)
    ]
    assert daily[1].metrics.sales_amount == Decimal("130")
    assert [row.branch_name for row in daily_branches] == ["Loma Linda", "Alianza"]


def test_iso_week_uses_monday_sunday_and_handles_year_boundary() -> None:
    assert iso_year_and_week(date(2025, 12, 29)) == (2026, 1)
    assert iso_year_and_week(date(2026, 1, 4)) == (2026, 1)
    assert iso_year_and_week(date(2026, 1, 5)) == (2026, 2)
    assert iso_week_bounds(2026, 1) == (date(2025, 12, 29), date(2026, 1, 4))


def test_week_has_all_seven_days_and_fills_missing_with_zero(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    points = weekly_daily_series(
        session, iso_year=2026, iso_week=1, metric="sales_amount"
    )

    assert [point.label for point in points] == [
        "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"
    ]
    assert [point.value for point in points] == [
        Decimal("80"), ZERO, ZERO, Decimal("130"), ZERO, Decimal("200"), ZERO
    ]


ZERO = Decimal(0)


def test_weekly_history_existing_and_complete_are_chronological(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    existing = weekly_series(session, iso_year=2026, metric="total_units")
    complete = weekly_series(
        session, iso_year=2026, metric="total_units", complete=True
    )

    assert [point.key for point in existing] == ["2026-W01", "2026-W06"]
    assert existing[0].value == Decimal("27")
    assert complete[0].key == "2026-W01"
    assert complete[-1].key == "2026-W53"
    assert complete[1].value == ZERO


def test_monthly_series_returns_january_to_december_with_zeros(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    points = monthly_series(session, year=2026, metric="sales_amount")

    assert len(points) == 12
    assert points[0].label == "Enero" and points[0].value == Decimal("330")
    assert points[1].label == "Febrero" and points[1].value == Decimal("84")
    assert all(point.value == ZERO for point in points[2:])


def test_branch_comparison_includes_branches_without_data_and_respects_filters(
    historical_data: dict[str, object],
) -> None:
    session, branches, _, products = _context(historical_data)

    rows = aggregate_by_branch(
        session, AggregationFilters(master_product_id=products["Agua"].id)
    )

    assert [row.branch_name for row in rows] == [
        "Loma Linda", "Solidaridad", "Alianza", "Unidad"
    ]
    assert rows[0].metrics.total_units == Decimal("12")
    assert rows[2].metrics.total_units == Decimal("5")
    assert rows[1].metrics.total_units == ZERO
    only_alianza = aggregate_by_branch(
        session, AggregationFilters(branch_id=branches["Alianza"].id)
    )
    assert [row.branch_name for row in only_alianza] == ["Alianza"]


def test_products_group_by_master_and_branch_filter_separates_local_mappings(
    historical_data: dict[str, object],
) -> None:
    session, branches, _, products = _context(historical_data)

    all_rows = aggregate_by_product(session)
    agua = next(row for row in all_rows if row.master_product_id == products["Agua"].id)
    loma_rows = aggregate_by_product(
        session, AggregationFilters(branch_id=branches["Loma Linda"].id)
    )
    loma_agua = next(row for row in loma_rows if row.master_product_id == products["Agua"].id)

    assert agua.metrics.total_units == Decimal("17")
    assert loma_agua.metrics.total_units == Decimal("12")
    assert not hasattr(agua, "external_product_id")


def test_categories_group_master_products(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    rows = aggregate_by_category(session)

    assert [row.category_name for row in rows] == ["Alimentos", "Bebidas"]
    assert rows[0].metrics.total_units == Decimal("14")
    assert rows[1].metrics.total_units == Decimal("21")


def test_product_name_and_numeric_sorting_with_stable_tie(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)

    assert [row.product_name for row in aggregate_by_product(session)] == [
        "Agua", "Pollo", "Yogur", "Zeta"
    ]
    assert [
        row.product_name
        for row in aggregate_by_product(session, sort_by="name", direction="desc")
    ] == ["Zeta", "Yogur", "Pollo", "Agua"]
    assert [
        row.product_name
        for row in aggregate_by_product(session, sort_by="total_units", direction="asc")
    ] == ["Yogur", "Zeta", "Pollo", "Agua"]
    assert [
        row.product_name
        for row in aggregate_by_product(session, sort_by="sales_amount", direction="desc")
    ] == ["Pollo", "Agua", "Yogur", "Zeta"]


def test_metric_and_sort_whitelists_reject_arbitrary_columns(
    historical_data: dict[str, object],
) -> None:
    session, _, _, _ = _context(historical_data)

    with pytest.raises(ValueError, match="métrica"):
        weekly_series(session, iso_year=2026, metric="drop_table")
    with pytest.raises(ValueError, match="orden"):
        aggregate_by_product(session, sort_by="external_product_id")
    with pytest.raises(ValueError, match="dirección"):
        aggregate_by_product(session, direction="sideways")


def test_invalid_dates_and_ranges_are_rejected() -> None:
    with pytest.raises(TypeError, match="fecha válida"):
        AggregationFilters(date_from="2026-01-01")
    with pytest.raises(ValueError, match="posterior"):
        AggregationFilters(date_from=date(2026, 2, 1), date_to=date(2026, 1, 1))
    with pytest.raises(ValueError, match="semana ISO"):
        iso_week_bounds(2026, 54)


def test_raw_issue_without_sale_is_reported_but_never_changes_sales_metrics(
    historical_data: dict[str, object],
) -> None:
    session, _, _, products = _context(historical_data)

    summary = summarize_issues(
        session, AggregationFilters(date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
    )
    product_summary = summarize_issues(
        session,
        AggregationFilters(master_product_id=products["Agua"].id),
    )
    jan_totals = aggregate_totals(
        session, AggregationFilters(date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
    )

    assert summary.raw_rows_with_issues == 1
    assert summary.affected_batches == 1
    assert product_summary.raw_rows_with_issues == 1
    assert jan_totals.total_units == Decimal("17")


def test_price_history_is_derived_without_new_table_and_can_show_changes_only(
    historical_data: dict[str, object],
) -> None:
    session, _, _, products = _context(historical_data)

    points = price_history(session, master_product_id=products["Agua"].id)

    assert [(point.sales_date, point.branch_name, point.sale_price) for point in points] == [
        (date(2025, 12, 29), "Loma Linda", Decimal("10")),
        (date(2026, 1, 1), "Alianza", Decimal("10")),
        (date(2026, 2, 2), "Loma Linda", Decimal("12")),
    ]
    assert "price_history" not in inspect(session.get_bind()).get_table_names()


def test_composite_index_supports_historical_filters(historical_data: dict[str, object]) -> None:
    session, _, _, _ = _context(historical_data)
    indexes = inspect(session.get_bind()).get_indexes("sale_records")

    assert any(
        index["name"] == "ix_sale_records_date_branch_master"
        and index["column_names"] == ["sales_date", "branch_id", "master_product_id"]
        for index in indexes
    )
