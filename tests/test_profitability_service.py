from collections.abc import Generator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db, seed_branches
from app.db.models import (
    Branch,
    BranchProduct,
    Category,
    ImportBatch,
    MasterProduct,
    ProductCost,
    RawSaleRecord,
    SaleRecord,
)
from app.domain.normalization import normalize_for_comparison
from app.main import app
from app.services.aggregation_service import AggregationFilters
from app.services.dashboard_service import build_dashboard
from app.services.profitability_service import (
    profitability_by_branch,
    profitability_by_category,
    profitability_by_product,
    profitability_daily,
    profitability_monthly,
    profitability_totals,
    profitability_weekly,
)
from app.services.reporting_service import (
    build_branches_report,
    build_product_detail_report,
    build_products_report,
)

REFERENCE_DAY = date(2026, 8, 31)


@pytest.fixture
def profit_session() -> Generator[tuple[Session, dict[str, object]], None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)
    branches = {row.name: row for row in session.scalars(select(Branch))}
    sides = Category(display_name="Complementos", normalized_name="complementos")
    other = Category(display_name="Otros", normalized_name="otros")
    names = (
        "Ensalada de col",
        "Producto combo",
        "Costo cambiante",
        "Sin costo",
        "Costo cero",
        "Pérdida",
        "Utilidad cero",
        "Venta cero",
        "Devolución",
    )
    products = {
        name: MasterProduct(
            display_name=name,
            normalized_name=normalize_for_comparison(name),
            category=sides if name in {"Ensalada de col", "Producto combo"} else other,
        )
        for name in names
    }
    session.add_all([sides, other, *products.values()])
    session.flush()
    mappings = {}
    number = 1
    for name, product in products.items():
        branch = branches["Loma Linda"]
        mappings[name] = _mapping(session, branch, product, f"P{number}")
        number += 1
    salad_solidarity = _mapping(
        session, branches["Solidaridad"], products["Ensalada de col"], "248"
    )
    salad_alliance = _mapping(
        session, branches["Alianza"], products["Ensalada de col"], "254"
    )
    sales = [
        (date(2026, 8, 12), salad_solidarity, "3", "2", "1", "98", "49", "900"),
        (date(2026, 8, 12), salad_alliance, "12", "4", "8", "196", "49", "800"),
        (date(2026, 8, 10), mappings["Producto combo"], "15", "10", "5", "100", "10", "700"),
        (date(2026, 8, 10), mappings["Costo cambiante"], "10", "10", "0", "100", "10", "600"),
        (date(2026, 8, 20), mappings["Costo cambiante"], "10", "10", "0", "100", "10", "600"),
        (date(2026, 8, 10), mappings["Sin costo"], "10", "10", "0", "100", "10", "500"),
        (date(2026, 8, 10), mappings["Costo cero"], "2", "2", "0", "50", "25", "400"),
        (date(2026, 8, 10), mappings["Pérdida"], "10", "10", "0", "100", "10", "300"),
        (date(2026, 8, 10), mappings["Utilidad cero"], "10", "10", "0", "100", "10", "200"),
        (date(2026, 8, 10), mappings["Venta cero"], "1", "0", "1", "0", "1", "100"),
        (date(2026, 8, 10), mappings["Devolución"], "-1", "-1", "0", "-20", "20", "50"),
    ]
    for index, values in enumerate(sales, start=1):
        _sale(session, index, *values)
    costs = {
        "Ensalada de col": [(date(2026, 8, 1), "10")],
        "Producto combo": [(date(2026, 8, 1), "4")],
        "Costo cambiante": [(date(2026, 8, 1), "4"), (date(2026, 8, 15), "5")],
        "Costo cero": [(date(2026, 8, 1), "0")],
        "Pérdida": [(date(2026, 8, 1), "20")],
        "Utilidad cero": [(date(2026, 8, 1), "10")],
        "Venta cero": [(date(2026, 8, 1), "5")],
        "Devolución": [(date(2026, 8, 1), "5")],
    }
    for name, history in costs.items():
        for effective_from, cost in history:
            session.add(
                ProductCost(
                    master_product=products[name],
                    effective_from=effective_from,
                    cost=Decimal(cost),
                )
            )
    session.commit()
    yield session, {
        "branches": branches,
        "categories": {"sides": sides, "other": other},
        "products": products,
    }
    session.close()


@pytest.fixture
def profit_client(profit_session) -> Generator[TestClient, None, None]:
    session, _ = profit_session

    def override_database() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_database
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _mapping(session, branch, product, external_id):
    row = BranchProduct(
        branch=branch,
        master_product=product,
        external_product_id=external_id,
        original_product_name=product.display_name,
        normalized_product_name=product.normalized_name,
        original_category_name=product.category.display_name,
    )
    session.add(row)
    session.flush()
    return row


def _sale(
    session,
    number,
    sales_date,
    mapping,
    total,
    individual,
    combo,
    amount,
    sale_price,
    original_cost,
):
    batch = ImportBatch(
        sales_date=sales_date,
        branch=mapping.branch,
        source_filename=f"profit_{number}.xlsx",
        stored_filename=f"profit_{number}_stored.xlsx",
        sha256=f"{number:064d}",
        status="COMPLETED",
        total_rows=1,
        normalized_rows=1,
    )
    raw = RawSaleRecord(
        import_batch=batch,
        source_row_number=2,
        external_product_id=mapping.external_product_id,
        original_product_name=mapping.original_product_name,
        quantity_raw=total,
        sale_price_raw=sale_price,
        cost_raw=original_cost,
        total_amount_raw=amount,
    )
    session.add(
        SaleRecord(
            batch=batch,
            raw_sale_record=raw,
            branch=mapping.branch,
            branch_product=mapping,
            master_product=mapping.master_product,
            sales_date=sales_date,
            total_units=Decimal(total),
            sale_price=Decimal(sale_price),
            original_cost=Decimal(original_cost),
            sales_amount=Decimal(amount),
            individual_sale_units=Decimal(individual),
            combo_units=Decimal(combo),
            source_row_number=2,
        )
    )


def _filters(context, product_name, start=date(2026, 8, 1), end=date(2026, 8, 31)):
    return AggregationFilters(
        date_from=start,
        date_to=end,
        master_product_id=context["products"][product_name].id,
    )


def test_combo_cost_uses_total_units_not_individual_units(profit_session) -> None:
    session, context = profit_session
    metrics = profitability_totals(session, _filters(context, "Producto combo"))
    assert metrics.total_sales_amount == Decimal("100")
    assert metrics.known_cost == Decimal("60")
    assert metrics.known_profit == Decimal("40")
    assert metrics.known_margin_percent == Decimal("40")


def test_historical_cost_change_is_applied_per_sale_date(profit_session) -> None:
    session, context = profit_session
    metrics = profitability_totals(session, _filters(context, "Costo cambiante"))
    assert metrics.known_cost == Decimal("90")
    assert metrics.known_profit == Decimal("110")
    assert metrics.known_margin_percent == Decimal("55")


def test_missing_cost_keeps_sale_visible_and_financials_partial(profit_session) -> None:
    session, context = profit_session
    metrics = profitability_totals(session, _filters(context, "Sin costo"))
    assert metrics.total_sales_amount == Decimal("100")
    assert metrics.sales_amount_without_cost == Decimal("100")
    assert metrics.records_without_cost == 1 and metrics.products_without_cost == 1
    assert metrics.known_cost == metrics.known_profit == Decimal("0")
    assert metrics.known_margin_percent is None
    assert metrics.sales_coverage_percent == Decimal("0")


def test_zero_cost_is_known_and_not_missing(profit_session) -> None:
    session, context = profit_session
    metrics = profitability_totals(session, _filters(context, "Costo cero"))
    assert metrics.records_with_cost == 1 and metrics.records_without_cost == 0
    assert metrics.known_cost == Decimal("0")
    assert metrics.known_profit == Decimal("50")
    assert metrics.sales_coverage_percent == Decimal("100")


@pytest.mark.parametrize(
    ("product", "profit", "margin"),
    [
        ("Ensalada de col", Decimal("144"), Decimal("48.97959183673469387755102041")),
        ("Pérdida", Decimal("-100"), Decimal("-100")),
        ("Utilidad cero", Decimal("0"), Decimal("0")),
    ],
)
def test_positive_negative_and_zero_profit_margin(profit_session, product, profit, margin) -> None:
    session, context = profit_session
    metrics = profitability_totals(session, _filters(context, product))
    assert metrics.known_profit == profit
    assert metrics.known_margin_percent == margin


def test_zero_sale_does_not_divide_and_negative_signs_are_preserved(profit_session) -> None:
    session, context = profit_session
    zero_sale = profitability_totals(session, _filters(context, "Venta cero"))
    refund = profitability_totals(session, _filters(context, "Devolución"))
    assert zero_sale.known_cost == Decimal("5")
    assert zero_sale.known_profit == Decimal("-5")
    assert zero_sale.known_margin_percent is None
    assert refund.total_sales_amount == Decimal("-20")
    assert refund.known_cost == Decimal("-5")
    assert refund.known_profit == Decimal("-15")


def test_aggregate_margin_is_recalculated_not_summed_or_averaged(profit_session) -> None:
    session, context = profit_session
    filters = _filters(context, "Ensalada de col")
    total = profitability_totals(session, filters)
    branches = profitability_by_branch(session, filters)
    nonempty = [row for row in branches if row.financial.total_records]
    assert total.known_cost == Decimal("150")
    assert total.known_profit == Decimal("144")
    assert total.known_margin_percent == Decimal("144") / Decimal("294") * Decimal("100")
    assert sum(row.financial.known_profit for row in nonempty) == Decimal("144")
    assert total.known_margin_percent != sum(row.financial.known_margin_percent for row in nonempty)


def test_branch_product_category_and_date_filters(profit_session) -> None:
    session, context = profit_session
    salad = context["products"]["Ensalada de col"]
    solidarity = context["branches"]["Solidaridad"]
    filtered = AggregationFilters(
        date(2026, 8, 12),
        date(2026, 8, 12),
        solidarity.id,
        salad.id,
        context["categories"]["sides"].id,
    )
    metrics = profitability_totals(session, filtered)
    assert metrics.total_sales_amount == Decimal("98")
    assert metrics.known_cost == Decimal("30")
    assert metrics.known_profit == Decimal("68")


def test_master_product_consolidation_and_salad_case(profit_session) -> None:
    session, context = profit_session
    salad = context["products"]["Ensalada de col"]
    rows = profitability_by_product(session, _filters(context, "Ensalada de col"))
    assert len(rows) == 1 and rows[0].master_product_id == salad.id
    assert rows[0].sales_metrics.total_units == Decimal("15")
    assert rows[0].financial.known_cost == Decimal("150")
    assert rows[0].financial.known_profit == Decimal("144")
    assert not hasattr(rows[0], "external_product_id")


def test_full_and_partial_coverage_metrics(profit_session) -> None:
    session, context = profit_session
    full = profitability_totals(session, _filters(context, "Producto combo"))
    partial = profitability_totals(
        session,
        AggregationFilters(date(2026, 8, 10), date(2026, 8, 10)),
    )
    assert full.sales_coverage_percent == full.record_coverage_percent == Decimal("100")
    assert partial.incomplete
    assert partial.sales_amount_without_cost == Decimal("100")
    assert partial.records_without_cost == 1
    assert partial.sales_coverage_percent < Decimal("100")


def test_daily_series_and_category_profitability(profit_session) -> None:
    session, context = profit_session
    changing = _filters(context, "Costo cambiante")
    daily = profitability_daily(session, changing)
    categories = profitability_by_category(
        session, AggregationFilters(date(2026, 8, 1), date(2026, 8, 31))
    )
    assert [(row.sales_date, row.financial.known_cost) for row in daily] == [
        (date(2026, 8, 10), Decimal("40")),
        (date(2026, 8, 20), Decimal("50")),
    ]
    assert {row.category_name for row in categories} == {"Complementos", "Otros"}


def test_weekly_and_monthly_margins_are_recalculated_per_bucket(profit_session) -> None:
    session, context = profit_session
    filters = _filters(context, "Costo cambiante")
    weekly = profitability_weekly(session, filters)
    monthly = profitability_monthly(session, filters)
    assert [row.financial.known_cost for row in weekly] == [Decimal("40"), Decimal("50")]
    assert monthly[0].financial.known_cost == Decimal("90")
    assert monthly[0].financial.known_margin_percent == Decimal("55")
    assert monthly[0].financial.known_margin_percent != sum(
        row.financial.known_margin_percent for row in weekly
    )


def test_original_cost_and_sale_price_are_ignored(profit_session) -> None:
    session, context = profit_session
    combo = context["products"]["Producto combo"]
    sale = session.scalar(select(SaleRecord).where(SaleRecord.master_product_id == combo.id))
    metrics = profitability_totals(session, _filters(context, "Producto combo"))
    assert sale.original_cost == Decimal("700") and sale.sale_price == Decimal("10")
    assert metrics.known_cost == Decimal("60")


def test_dashboard_and_reports_are_financially_consistent(profit_session) -> None:
    session, context = profit_session
    product = context["products"]["Ensalada de col"]
    dashboard = build_dashboard(
        session,
        period="custom",
        date_from="2026-08-01",
        date_to="2026-08-31",
        product=str(product.id),
        today=REFERENCE_DAY,
    )
    products = build_products_report(
        session,
        date_from="2026-08-01",
        date_to="2026-08-31",
        today=REFERENCE_DAY,
    )
    detail = build_product_detail_report(
        session,
        master_product_id=product.id,
        date_from="2026-08-01",
        date_to="2026-08-31",
        today=REFERENCE_DAY,
    )
    expected = profitability_totals(session, _filters(context, "Ensalada de col"))
    assert dashboard.financial == expected
    assert products.product_financial[product.id].financial == expected
    assert detail.financial == expected
    assert detail.has_cost_changes is False


def test_product_financial_sort_and_branch_report(profit_session) -> None:
    session, _ = profit_session
    products = build_products_report(
        session,
        date_from="2026-08-01",
        date_to="2026-08-31",
        sort_by="known_profit",
        direction="desc",
        today=REFERENCE_DAY,
    )
    branches = build_branches_report(
        session,
        date_from="2026-08-01",
        date_to="2026-08-31",
        today=REFERENCE_DAY,
    )
    profits = [products.product_financial[row.master_product_id].financial.known_profit for row in products.rows]
    assert profits == sorted(profits, reverse=True)
    assert len(branches.branch_financial) == 4
    assert branches.financial.total_sales_amount == products.financial.total_sales_amount


def test_financial_ui_labels_partial_warning_and_cost_changes(profit_client, profit_session) -> None:
    _, context = profit_session
    dashboard = profit_client.get("/?period=custom&date_from=2026-08-01&date_to=2026-08-31")
    products = profit_client.get("/reports/products?date_from=2026-08-01&date_to=2026-08-31&sort=known_profit")
    branches = profit_client.get("/reports/branches?date_from=2026-08-01&date_to=2026-08-31")
    changing = profit_client.get(
        f"/reports/products/{context['products']['Costo cambiante'].id}?date_from=2026-08-01&date_to=2026-08-31"
    )
    for response in (dashboard, products, branches, changing):
        assert response.status_code == 200
        for label in ("Costo", "Utilidad", "Margen", "Cobertura"):
            assert label in response.text
    assert "La utilidad mostrada es parcial" in dashboard.text
    assert "Rentabilidad por categoría" in products.text
    assert "El costo oficial cambió dentro de este periodo" in changing.text
