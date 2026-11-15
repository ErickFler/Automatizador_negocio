from collections.abc import Generator
from datetime import date
from decimal import Decimal
from html import unescape
import json
import re

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
from app.services.dashboard_service import (
    DashboardInputError,
    build_dashboard,
    resolve_period,
)

REFERENCE_DAY = date(2026, 8, 16)


@pytest.fixture
def dashboard_session() -> Generator[tuple[Session, dict[str, object]], None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)
    branches = {
        item.name: item for item in session.scalars(select(Branch).order_by(Branch.id))
    }
    category = Category(
        display_name="Complementos",
        normalized_name=normalize_for_comparison("Complementos"),
    )
    other_category = Category(
        display_name="Pollos", normalized_name=normalize_for_comparison("Pollos")
    )
    salad = MasterProduct(
        display_name="Ensalada de col",
        normalized_name=normalize_for_comparison("Ensalada de col"),
        category=category,
    )
    chicken = MasterProduct(
        display_name="Pollo familiar",
        normalized_name=normalize_for_comparison("Pollo familiar"),
        category=other_category,
    )
    session.add_all([category, other_category, salad, chicken])
    session.flush()
    mappings = {
        "Solidaridad": _mapping(session, branches["Solidaridad"], salad, "248"),
        "Alianza": _mapping(session, branches["Alianza"], salad, "254"),
        "Loma Linda": _mapping(session, branches["Loma Linda"], chicken, "500"),
    }
    counter = 1
    counter = _sale(
        session, counter, date(2026, 8, 12), mappings["Solidaridad"], "3", "2", "1", "98"
    )
    counter = _sale(
        session, counter, date(2026, 8, 12), mappings["Alianza"], "12", "4", "8", "196"
    )
    counter = _sale(
        session, counter, date(2026, 8, 16), mappings["Loma Linda"], "1", "1", "0", "20"
    )
    counter = _sale(
        session, counter, date(2026, 8, 5), mappings["Loma Linda"], "2", "2", "0", "40"
    )
    counter = _sale(
        session, counter, date(2026, 7, 15), mappings["Loma Linda"], "4", "4", "0", "80"
    )
    _issue_and_pending_batches(session, counter, branches["Unidad"])
    session.commit()
    context = {
        "branches": branches,
        "category": category,
        "other_category": other_category,
        "salad": salad,
        "chicken": chicken,
    }
    yield session, context
    session.close()


@pytest.fixture
def dashboard_client(
    dashboard_session: tuple[Session, dict[str, object]],
) -> Generator[tuple[TestClient, Session, dict[str, object]], None, None]:
    session, context = dashboard_session

    def override_database() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_database
    with TestClient(app) as client:
        yield client, session, context
    app.dependency_overrides.clear()


def _mapping(
    session: Session, branch: Branch, product: MasterProduct, external_id: str
) -> BranchProduct:
    mapping = BranchProduct(
        branch=branch,
        master_product=product,
        external_product_id=external_id,
        original_product_name=f"LOCAL {external_id}",
        normalized_product_name=normalize_for_comparison(f"LOCAL {external_id}"),
        original_category_name=product.category.display_name,
    )
    session.add(mapping)
    session.flush()
    return mapping


def _sale(
    session: Session,
    number: int,
    sales_date: date,
    mapping: BranchProduct,
    total_units: str,
    individual_units: str,
    combo_units: str,
    sales_amount: str,
) -> int:
    batch = ImportBatch(
        sales_date=sales_date,
        branch=mapping.branch,
        source_filename=f"dashboard_{number}.xlsx",
        stored_filename=f"dashboard_{number}_stored.xlsx",
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
        quantity_raw=total_units,
        sale_price_raw="49",
        total_amount_raw=sales_amount,
    )
    session.add(
        SaleRecord(
            batch=batch,
            raw_sale_record=raw,
            branch=mapping.branch,
            branch_product=mapping,
            master_product=mapping.master_product,
            sales_date=sales_date,
            total_units=Decimal(total_units),
            sale_price=Decimal("49"),
            sales_amount=Decimal(sales_amount),
            individual_sale_units=Decimal(individual_units),
            combo_units=Decimal(combo_units),
            source_row_number=2,
        )
    )
    return number + 1


def _issue_and_pending_batches(
    session: Session, number: int, branch: Branch
) -> None:
    issue_batch = ImportBatch(
        sales_date=date(2026, 8, 12),
        branch=branch,
        source_filename="issue.xlsx",
        stored_filename="issue_stored.xlsx",
        sha256=f"{number:064d}",
        status="COMPLETED_WITH_WARNINGS",
        total_rows=1,
        issue_rows=1,
        skipped_rows=1,
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
            stored_filename="pending_stored.xlsx",
            sha256=f"{number + 1:064d}",
            status="WAITING_FOR_PRODUCT_MAPPING",
            total_rows=1,
        )
    )


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("today", (date(2026, 8, 16), date(2026, 8, 16))),
        ("this_week", (date(2026, 8, 10), date(2026, 8, 16))),
        ("previous_week", (date(2026, 8, 3), date(2026, 8, 9))),
        ("this_month", (date(2026, 8, 1), date(2026, 8, 31))),
        ("previous_month", (date(2026, 7, 1), date(2026, 7, 31))),
        ("this_year", (date(2026, 1, 1), date(2026, 12, 31))),
    ],
)
def test_quick_periods(period: str, expected: tuple[date, date]) -> None:
    assert resolve_period(period, today=REFERENCE_DAY) == expected


def test_custom_period_and_invalid_ranges() -> None:
    assert resolve_period(
        "custom", today=REFERENCE_DAY, date_from="2026-08-01", date_to="2026-08-16"
    ) == (date(2026, 8, 1), date(2026, 8, 16))
    with pytest.raises(DashboardInputError, match="fecha inicial"):
        resolve_period(
            "custom", today=REFERENCE_DAY, date_from="2026-08-20", date_to="2026-08-10"
        )


def test_default_period_and_safe_whitelist_fallbacks(
    dashboard_session: tuple[Session, dict[str, object]],
) -> None:
    session, _ = dashboard_session

    default = build_dashboard(session, today=REFERENCE_DAY)
    safe = build_dashboard(
        session,
        today=REFERENCE_DAY,
        period="DROP TABLE",
        metric="secret_column",
        sort_by="external_product_id",
        direction="sideways",
    )

    assert default.selection.period == "this_month"
    assert default.selection.date_from == date(2026, 8, 1)
    assert safe.selection.period == "this_month"
    assert safe.selection.metric == "total_units"
    assert safe.selection.sort_by == "sales_amount"
    assert safe.selection.direction == "desc"
    assert safe.input_warning


def test_multi_branch_cards_and_filters_use_master_product(
    dashboard_session: tuple[Session, dict[str, object]],
) -> None:
    session, context = dashboard_session
    branches = context["branches"]
    salad = context["salad"]
    common = dict(
        period="custom",
        date_from="2026-08-12",
        date_to="2026-08-12",
        product=str(salad.id),
        today=REFERENCE_DAY,
    )

    all_branches = build_dashboard(session, **common)
    solidarity = build_dashboard(
        session, branch=str(branches["Solidaridad"].id), **common
    )
    alliance = build_dashboard(session, branch=str(branches["Alianza"].id), **common)

    assert all_branches.totals == type(all_branches.totals)(
        Decimal("15"), Decimal("6"), Decimal("9"), Decimal("294")
    )
    assert solidarity.totals == type(solidarity.totals)(
        Decimal("3"), Decimal("2"), Decimal("1"), Decimal("98")
    )
    assert alliance.totals == type(alliance.totals)(
        Decimal("12"), Decimal("4"), Decimal("8"), Decimal("196")
    )
    assert [row.product_name for row in all_branches.product_rows] == [
        "Ensalada de col"
    ]
    assert not hasattr(all_branches.product_rows[0], "external_product_id")


def test_category_product_branch_and_combined_filters(
    dashboard_session: tuple[Session, dict[str, object]],
) -> None:
    session, context = dashboard_session
    branches = context["branches"]
    category = context["category"]
    salad = context["salad"]

    view = build_dashboard(
        session,
        period="this_month",
        branch=str(branches["Solidaridad"].id),
        category=str(category.id),
        product=str(salad.id),
        today=REFERENCE_DAY,
    )

    assert view.totals.sales_amount == Decimal("98")
    assert [product.display_name for product in view.products] == ["Ensalada de col"]


def test_chart_data_has_week_days_zeros_months_and_selected_metric(
    dashboard_session: tuple[Session, dict[str, object]],
) -> None:
    session, _ = dashboard_session

    week = build_dashboard(
        session, period="this_week", metric="sales_amount", today=REFERENCE_DAY
    )
    year = build_dashboard(
        session, period="this_year", metric="combo_units", today=REFERENCE_DAY
    )

    assert len(week.temporal_chart.labels) == 7
    assert week.temporal_chart.labels[0] == "Lunes"
    assert week.temporal_chart.values == (
        "0", "0", "294.000000000000", "0", "0", "0", "20.000000000000"
    )
    assert len(year.temporal_chart.labels) == 12
    assert year.temporal_chart.metric_label == "Unidades combo"
    assert year.temporal_chart.values[7] == "9.000000000000"


def test_no_data_issues_pending_and_product_order(
    dashboard_session: tuple[Session, dict[str, object]],
) -> None:
    session, _ = dashboard_session

    current = build_dashboard(session, period="this_month", today=REFERENCE_DAY)
    empty = build_dashboard(
        session,
        period="custom",
        date_from="2030-01-01",
        date_to="2030-01-31",
        today=REFERENCE_DAY,
    )

    assert current.issue_summary.raw_rows_with_issues == 1
    assert current.pending_batches == 1
    assert current.selection.sort_by == "sales_amount"
    assert current.selection.direction == "desc"
    assert not empty.has_sales
    assert empty.totals.sales_amount == Decimal(0)


def test_dashboard_html_contains_controls_cards_charts_table_and_safe_json(
    dashboard_client: tuple[TestClient, Session, dict[str, object]],
) -> None:
    client, _, _ = dashboard_client

    response = client.get(
        "/?period=custom&date_from=2026-08-12&date_to=2026-08-12&metric=sales_amount"
    )
    chart_data = _chart_json(response.text)

    assert response.status_code == 200
    for expected in (
        "Control y análisis de ventas",
        'name="period"',
        'name="branch"',
        'name="category"',
        'name="product"',
        'name="metric"',
        "Aplicar filtros",
        "Limpiar filtros",
        "Venta",
        "Unidades totales",
        "Unidades venta individual",
        "Unidades combo",
        'id="temporal-chart"',
        'id="branch-chart"',
        "Comparativo de sucursales",
        "Productos",
        "Cargar ventas",
    ):
        assert expected in response.text
    assert "$294.00" in response.text
    assert "15" in response.text and "Ensalada de col" in response.text
    assert "LOCAL 248" not in response.text and "LOCAL 254" not in response.text
    assert chart_data["temporal"]["metric_label"] == "Venta $"


def test_dashboard_http_filters_invalid_custom_and_empty_state(
    dashboard_client: tuple[TestClient, Session, dict[str, object]],
) -> None:
    client, _, context = dashboard_client
    branches = context["branches"]

    solidarity = client.get(
        "/?period=custom&date_from=2026-08-12&date_to=2026-08-12"
        f"&branch={branches['Solidaridad'].id}"
    )
    invalid = client.get(
        "/?period=custom&date_from=2026-08-20&date_to=2026-08-10"
    )
    empty = client.get(
        "/?period=custom&date_from=2030-01-01&date_to=2030-01-31"
    )

    assert solidarity.status_code == 200 and "$98.00" in solidarity.text
    assert invalid.status_code == 400
    assert "fecha inicial no puede ser posterior" in invalid.text
    assert empty.status_code == 200
    assert "No hay ventas registradas para este periodo y filtros" in empty.text
    assert "$0.00" in empty.text


def test_table_headers_use_safe_service_sort_links(
    dashboard_client: tuple[TestClient, Session, dict[str, object]],
) -> None:
    client, _, _ = dashboard_client

    response = client.get("/?period=this_month&sort=not_allowed&direction=sideways")

    assert response.status_code == 200
    assert "se ordenó por Venta $" in response.text
    assert "sort=total_units" in response.text
    assert "sort=category" in response.text
    assert "sort=individual_sale_units" in response.text
    assert "sort=combo_units" in response.text
    assert "sort=sales_amount" in response.text
    assert 'aria-sort="descending"' in response.text


def _chart_json(html: str) -> dict[str, object]:
    match = re.search(
        r'<script id="dashboard-chart-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(unescape(match.group(1)))
