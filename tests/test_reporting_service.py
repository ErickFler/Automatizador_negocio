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
    ProcessingIssue,
    RawSaleRecord,
    SaleRecord,
)
from app.domain.normalization import normalize_for_comparison
from app.main import app
from app.services.aggregation_service import AggregationFilters, Metrics, aggregate_totals
from app.services.dashboard_service import build_dashboard
from app.services.reporting_service import (
    build_branches_report,
    build_daily_report,
    build_monthly_report,
    build_products_report,
    build_weekly_history_report,
    build_weekly_report,
)

REFERENCE_DAY = date(2026, 8, 16)


@pytest.fixture
def reporting_session() -> Generator[tuple[Session, dict[str, object]], None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)
    branches = {
        row.name: row for row in session.scalars(select(Branch).order_by(Branch.id))
    }
    sides = Category(
        display_name="Complementos",
        normalized_name=normalize_for_comparison("Complementos"),
    )
    meals = Category(
        display_name="Pollos", normalized_name=normalize_for_comparison("Pollos")
    )
    salad = MasterProduct(
        display_name="Ensalada de col",
        normalized_name=normalize_for_comparison("Ensalada de col"),
        category=sides,
    )
    chicken = MasterProduct(
        display_name="Pollo familiar",
        normalized_name=normalize_for_comparison("Pollo familiar"),
        category=meals,
    )
    session.add_all([sides, meals, salad, chicken])
    session.flush()
    solidarity = _mapping(session, branches["Solidaridad"], salad, "248")
    alliance = _mapping(session, branches["Alianza"], salad, "254")
    loma = _mapping(session, branches["Loma Linda"], chicken, "500")
    number = 1
    number = _sale(session, number, date(2026, 8, 12), solidarity, "3", "2", "1", "98", "49")
    number = _sale(session, number, date(2026, 8, 12), alliance, "12", "4", "8", "196", "49")
    number = _sale(session, number, date(2026, 8, 16), loma, "1", "1", "0", "20", "20")
    number = _sale(session, number, date(2026, 8, 5), loma, "2", "2", "0", "40", "20")
    number = _sale(session, number, date(2026, 7, 15), loma, "4", "4", "0", "80", "20")
    number = _sale(session, number, date(2025, 12, 31), loma, "5", "5", "0", "100", "20")
    _issue_and_pending(session, number, branches["Unidad"])
    session.commit()
    context = {
        "branches": branches,
        "sides": sides,
        "meals": meals,
        "salad": salad,
        "chicken": chicken,
    }
    yield session, context
    session.close()


@pytest.fixture
def reporting_client(
    reporting_session: tuple[Session, dict[str, object]],
) -> Generator[tuple[TestClient, Session, dict[str, object]], None, None]:
    session, context = reporting_session

    def override_database() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_database
    with TestClient(app) as client:
        yield client, session, context
    app.dependency_overrides.clear()


def _mapping(session: Session, branch: Branch, product: MasterProduct, external_id: str) -> BranchProduct:
    row = BranchProduct(
        branch=branch,
        master_product=product,
        external_product_id=external_id,
        original_product_name=f"LOCAL {external_id}",
        normalized_product_name=normalize_for_comparison(f"LOCAL {external_id}"),
        original_category_name=product.category.display_name,
    )
    session.add(row)
    session.flush()
    return row


def _sale(
    session: Session,
    number: int,
    sales_date: date,
    mapping: BranchProduct,
    total: str,
    individual: str,
    combo: str,
    amount: str,
    price: str,
) -> int:
    batch = ImportBatch(
        sales_date=sales_date,
        branch=mapping.branch,
        source_filename=f"report_{number}.xlsx",
        stored_filename=f"report_{number}_stored.xlsx",
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
        sale_price_raw=price,
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
            sale_price=Decimal(price),
            sales_amount=Decimal(amount),
            individual_sale_units=Decimal(individual),
            combo_units=Decimal(combo),
            source_row_number=2,
        )
    )
    return number + 1


def _issue_and_pending(session: Session, number: int, branch: Branch) -> None:
    issue_batch = ImportBatch(
        sales_date=date(2026, 8, 12),
        branch=branch,
        source_filename="issue.xlsx",
        stored_filename="report_issue.xlsx",
        sha256=f"{number:064d}",
        status="COMPLETED_WITH_WARNINGS",
        total_rows=1,
        issue_rows=1,
    )
    raw = RawSaleRecord(
        import_batch=issue_batch,
        source_row_number=2,
        external_product_id="999",
        original_product_name="PRECIO CERO",
        quantity_raw="3",
        sale_price_raw="0",
        total_amount_raw="0",
    )
    session.add(
        ProcessingIssue(
            import_batch=issue_batch,
            raw_sale_record=raw,
            source_row_number=2,
            issue_type="ZERO_SALE_PRICE",
            severity="ERROR",
            message="PrecioVenta es cero.",
        )
    )
    session.add(
        ImportBatch(
            sales_date=date(2026, 8, 12),
            branch=branch,
            source_filename="pending.xlsx",
            stored_filename="report_pending.xlsx",
            sha256=f"{number + 1:064d}",
            status="WAITING_FOR_PRODUCT_MAPPING",
            total_rows=1,
        )
    )


def test_reports_index_lists_six_reports(reporting_client) -> None:
    client, _, _ = reporting_client
    response = client.get("/reports")
    assert response.status_code == 200
    for text in (
        "Ventas diarias",
        "Semana lunes a domingo",
        "Histórico semanal",
        "Histórico mensual",
        "Productos",
        "Comparativo de sucursales",
    ):
        assert text in response.text


def test_daily_date_totals_rows_prices_and_master_identity(reporting_session) -> None:
    session, context = reporting_session
    report = build_daily_report(session, selected_date="2026-08-12", today=REFERENCE_DAY)
    assert report.sales_date == date(2026, 8, 12)
    assert report.totals == Metrics(Decimal("15"), Decimal("6"), Decimal("9"), Decimal("294"))
    assert [(row.branch_name, row.metrics.total_units) for row in report.rows] == [
        ("Alianza", Decimal("12")),
        ("Solidaridad", Decimal("3")),
    ]
    assert {row.master_product_id for row in report.rows} == {context["salad"].id}
    assert all(row.observed_sale_prices == (Decimal("49"),) for row in report.rows)
    assert not hasattr(report.rows[0], "external_product_id")


def test_daily_combined_filters_and_no_data(reporting_session) -> None:
    session, context = reporting_session
    branch = context["branches"]["Solidaridad"]
    filtered = build_daily_report(
        session,
        selected_date="2026-08-12",
        branch=str(branch.id),
        category=str(context["sides"].id),
        product=str(context["salad"].id),
        today=REFERENCE_DAY,
    )
    empty = build_daily_report(session, selected_date="2030-01-01", today=REFERENCE_DAY)
    assert filtered.totals == Metrics(Decimal("3"), Decimal("2"), Decimal("1"), Decimal("98"))
    assert len(filtered.rows) == 1 and filtered.rows[0].branch_name == "Solidaridad"
    assert empty.totals == Metrics() and not empty.rows


def test_week_is_monday_to_sunday_with_seven_rows_and_zeros(reporting_session) -> None:
    session, _ = reporting_session
    report = build_weekly_report(session, selected_date="2026-08-12", today=REFERENCE_DAY)
    assert report.context.filters.date_from == date(2026, 8, 10)
    assert report.context.filters.date_to == date(2026, 8, 16)
    assert len(report.day_rows) == 7
    assert [row.sales_date.weekday() for row in report.day_rows] == list(range(7))
    assert report.day_rows[0].metrics == Metrics()
    assert report.day_rows[2].metrics.sales_amount == Decimal("294")
    assert report.day_rows[6].metrics.sales_amount == Decimal("20")


def test_week_filters_and_product_sort_links(reporting_session) -> None:
    session, context = reporting_session
    report = build_weekly_report(
        session,
        selected_date="2026-08-12",
        category=str(context["sides"].id),
        sort_by="name",
        direction="asc",
        today=REFERENCE_DAY,
    )
    assert report.totals.sales_amount == Decimal("294")
    assert [row.product_name for row in report.product_rows] == ["Ensalada de col"]
    assert "sort=combo_units" in report.sort_links["combo_units"]


def test_weekly_history_is_ordered_iso_and_handles_year_crossing(reporting_session) -> None:
    session, _ = reporting_session
    report = build_weekly_history_report(session, year="2026", today=REFERENCE_DAY)
    assert report.rows[0].key == "2026-W01"
    assert report.rows[0].period_start == date(2025, 12, 29)
    assert report.rows[0].period_end == date(2026, 1, 4)
    assert report.rows[0].metrics.sales_amount == Decimal("100")
    assert [row.period_start for row in report.rows] == sorted(row.period_start for row in report.rows)


def test_weekly_history_filters(reporting_session) -> None:
    session, context = reporting_session
    report = build_weekly_history_report(
        session,
        year="2026",
        branch=str(context["branches"]["Solidaridad"].id),
        product=str(context["salad"].id),
        metric="combo_units",
        today=REFERENCE_DAY,
    )
    august_week = next(row for row in report.rows if row.period_start == date(2026, 8, 10))
    assert report.metric == "combo_units"
    assert august_week.metrics == Metrics(Decimal("3"), Decimal("2"), Decimal("1"), Decimal("98"))


def test_monthly_always_has_january_to_december_and_zeros(reporting_session) -> None:
    session, _ = reporting_session
    report = build_monthly_report(session, year="2026", today=REFERENCE_DAY)
    assert len(report.rows) == 12
    assert [row.label for row in report.rows][0::11] == ["Enero", "Diciembre"]
    assert report.rows[0].metrics == Metrics()
    assert report.rows[6].metrics.sales_amount == Decimal("80")
    assert report.rows[7].metrics.sales_amount == Decimal("354")


def test_monthly_filters(reporting_session) -> None:
    session, context = reporting_session
    report = build_monthly_report(
        session,
        year="2026",
        category=str(context["sides"].id),
        branch=str(context["branches"]["Alianza"].id),
        today=REFERENCE_DAY,
    )
    assert report.rows[7].metrics == Metrics(Decimal("12"), Decimal("4"), Decimal("8"), Decimal("196"))
    assert all(row.metrics == Metrics() for row in report.rows[:7])


def test_products_group_by_master_and_sort_name_and_numeric(reporting_session) -> None:
    session, context = reporting_session
    common = dict(date_from="2026-08-01", date_to="2026-08-31", today=REFERENCE_DAY)
    by_name = build_products_report(session, sort_by="name", direction="asc", **common)
    by_sales = build_products_report(session, sort_by="sales_amount", direction="desc", **common)
    assert [row.product_name for row in by_name.rows] == ["Ensalada de col", "Pollo familiar"]
    assert [row.product_name for row in by_sales.rows] == ["Ensalada de col", "Pollo familiar"]
    salad = by_sales.rows[0]
    assert salad.master_product_id == context["salad"].id
    assert salad.metrics == Metrics(Decimal("15"), Decimal("6"), Decimal("9"), Decimal("294"))


def test_products_category_and_branch_filters(reporting_session) -> None:
    session, context = reporting_session
    report = build_products_report(
        session,
        date_from="2026-08-01",
        date_to="2026-08-31",
        branch=str(context["branches"]["Solidaridad"].id),
        category=str(context["sides"].id),
        today=REFERENCE_DAY,
    )
    assert len(report.rows) == 1
    assert report.rows[0].product_name == "Ensalada de col"
    assert report.rows[0].metrics.sales_amount == Decimal("98")


def test_branches_always_show_four_with_zeros_and_filters(reporting_session) -> None:
    session, context = reporting_session
    report = build_branches_report(
        session,
        date_from="2026-08-12",
        date_to="2026-08-12",
        category=str(context["sides"].id),
        product=str(context["salad"].id),
        today=REFERENCE_DAY,
    )
    assert [row.branch_name for row in report.rows] == ["Loma Linda", "Solidaridad", "Alianza", "Unidad"]
    values = {row.branch_name: row.metrics for row in report.rows}
    assert values["Solidaridad"] == Metrics(Decimal("3"), Decimal("2"), Decimal("1"), Decimal("98"))
    assert values["Alianza"] == Metrics(Decimal("12"), Decimal("4"), Decimal("8"), Decimal("196"))
    assert values["Loma Linda"] == Metrics() and values["Unidad"] == Metrics()


def test_dashboard_report_and_aggregation_totals_are_identical(reporting_session) -> None:
    session, _ = reporting_session
    dashboard = build_dashboard(
        session,
        period="custom",
        date_from="2026-08-10",
        date_to="2026-08-16",
        today=REFERENCE_DAY,
    )
    report = build_weekly_report(session, selected_date="2026-08-12", today=REFERENCE_DAY)
    engine = aggregate_totals(
        session, AggregationFilters(date(2026, 8, 10), date(2026, 8, 16))
    )
    assert dashboard.totals == report.totals == engine


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/reports/daily?date=2026-08-12", "Detalle por producto y sucursal"),
        ("/reports/weekly?date=2026-08-12", "Semana completa"),
        ("/reports/weekly-history?year=2026", "Semanas ISO de 2026"),
        ("/reports/monthly?year=2026", "Enero a diciembre de 2026"),
        ("/reports/products?date_from=2026-08-01&date_to=2026-08-31", "Productos del periodo"),
        ("/reports/branches?date_from=2026-08-12&date_to=2026-08-12", "Las cuatro sucursales"),
    ],
)
def test_six_report_pages_render_and_navigate_back(reporting_client, path, expected) -> None:
    client, _, _ = reporting_client
    response = client.get(path)
    assert response.status_code == 200
    assert expected in response.text
    assert "Volver a Reportes" in response.text
    assert 'href="/reports"' in response.text


def test_daily_html_has_summary_alerts_and_no_local_ids(reporting_client) -> None:
    client, _, _ = reporting_client
    response = client.get("/reports/daily?date=2026-08-12")
    assert "$294.00" in response.text
    assert "01/08" not in response.text
    assert "12/08/2026 al 12/08/2026" in response.text
    assert "Este periodo contiene registros con incidencias" in response.text
    assert "Hay cargas pendientes de confirmar" in response.text
    assert "LOCAL 248" not in response.text and "LOCAL 254" not in response.text


def test_empty_period_has_clear_state(reporting_client) -> None:
    client, _, _ = reporting_client
    response = client.get("/reports/daily?date=2030-01-01")
    assert response.status_code == 200
    assert "No hay información registrada para los filtros seleccionados" in response.text


def test_product_detail_shows_equivalences_prices_and_metrics(reporting_client) -> None:
    client, _, context = reporting_client
    response = client.get(
        f"/reports/products/{context['salad'].id}?date_from=2026-08-01&date_to=2026-08-31"
    )
    assert response.status_code == 200
    assert "Ensalada de col" in response.text
    assert "Equivalencias por sucursal" in response.text
    assert "Precios observados" in response.text
    assert "$294.00" in response.text
    assert "LOCAL 248" in response.text and "LOCAL 254" in response.text


def test_product_detail_not_found(reporting_client) -> None:
    client, _, _ = reporting_client
    assert client.get("/reports/products/999999").status_code == 404
