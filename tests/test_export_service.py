from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import create_engine
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
    ProductCost,
    RawSaleRecord,
    SaleRecord,
)
from app.main import app
from app.services.dashboard_service import build_dashboard
from app.services.export_service import (
    EXCEL_MEDIA_TYPE,
    MONEY_FORMAT,
    PARTIAL_WARNING,
    PENDING_WARNING,
    PERCENT_FORMAT,
    export_branches_workbook,
    export_daily_workbook,
    export_monthly_workbook,
    export_product_detail_workbook,
    export_products_workbook,
    export_weekly_history_workbook,
    export_weekly_workbook,
)
from app.services.reporting_service import (
    build_branches_report,
    build_daily_report,
    build_monthly_report,
    build_product_detail_report,
    build_products_report,
    build_weekly_history_report,
    build_weekly_report,
)

TODAY = date(2026, 8, 31)
GENERATED = datetime(2026, 8, 31, 12, 30)


@pytest.fixture
def export_session() -> Generator[tuple[Session, dict[str, object]], None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)
    branches = {row.name: row for row in session.query(Branch).all()}
    category = Category(display_name="Guarniciones ñ", normalized_name="guarniciones n")
    salad = MasterProduct(
        display_name="Ensalada de col ácida",
        normalized_name="ensalada de col acida",
        category=category,
    )
    missing = MasterProduct(
        display_name="Producto sin costo",
        normalized_name="producto sin costo",
        category=category,
    )
    session.add_all((category, salad, missing))
    session.flush()
    solidarity = _mapping(session, branches["Solidaridad"], salad, "248")
    alliance = _mapping(session, branches["Alianza"], salad, "254")
    missing_mapping = _mapping(session, branches["Loma Linda"], missing, "SC-1")
    first_raw = _sale(session, 1, date(2026, 8, 12), solidarity, "3", "2", "1", "98", "49")
    _sale(session, 2, date(2026, 8, 12), alliance, "12", "4", "8", "196", "49")
    _sale(session, 3, date(2026, 8, 13), missing_mapping, "10", "10", "0", "100", "10")
    session.add(ProductCost(master_product=salad, effective_from=date(2026, 8, 1), cost=Decimal("10")))
    session.add(
        ProcessingIssue(
            import_batch=first_raw.import_batch,
            raw_sale_record=first_raw,
            source_row_number=2,
            issue_type="TEST_WARNING",
            severity="WARNING",
            message="Revisar descripción con acento: válido.",
        )
    )
    pending = ImportBatch(
        sales_date=date(2026, 8, 14),
        branch=branches["Solidaridad"],
        source_filename="pendiente.xlsx",
        stored_filename="pendiente-stored.xlsx",
        sha256="f" * 64,
        status="WAITING_FOR_PRODUCT_MAPPING",
        total_rows=1,
    )
    session.add(pending)
    session.commit()
    yield session, {"branches": branches, "category": category, "salad": salad, "missing": missing}
    session.close()


@pytest.fixture
def export_client(export_session) -> Generator[TestClient, None, None]:
    session, _ = export_session

    def override() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _mapping(session, branch, product, external_id):
    mapping = BranchProduct(
        branch=branch,
        master_product=product,
        external_product_id=external_id,
        original_product_name=product.display_name,
        normalized_product_name=product.normalized_name,
        original_category_name=product.category.display_name,
    )
    session.add(mapping)
    session.flush()
    return mapping


def _sale(session, number, sales_date, mapping, total, individual, combo, amount, price):
    batch = ImportBatch(
        sales_date=sales_date,
        branch=mapping.branch,
        source_filename=f"venta-{number}.xlsx",
        stored_filename=f"venta-{number}-stored.xlsx",
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
        original_category_name=mapping.original_category_name,
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
    session.flush()
    return raw


def _workbook(export):
    assert export.content.startswith(b"PK")
    return load_workbook(BytesIO(export.content))


def _header_row(sheet: Worksheet, first_header: str) -> int:
    found = None
    for row in range(1, sheet.max_row + 1):
        if sheet.cell(row, 1).value == first_header:
            found = row
    if found is not None:
        return found
    raise AssertionError(f"No se encontró encabezado {first_header}")


def _row_by_first_value(sheet: Worksheet, value: str, start: int) -> tuple:
    for row in sheet.iter_rows(min_row=start + 1, values_only=True):
        if row[0] == value:
            return row
    raise AssertionError(f"No se encontró fila {value}")


def test_daily_workbook_is_valid_formatted_unicode_and_summarized(export_session) -> None:
    session, context = export_session
    report = build_daily_report(
        session,
        selected_date="2026-08-12",
        category=str(context["category"].id),
        today=TODAY,
    )
    exported = export_daily_workbook(report, generated_at=GENERATED)
    workbook = _workbook(exported)
    sheet = workbook["Ventas diarias"]
    header = _header_row(sheet, "Producto")
    assert exported.filename == "Ventas_Diarias_2026-08-12.xlsx"
    assert sheet["B1"].value == "Ventas diarias"
    assert sheet["B2"].value == "12/08/2026 al 12/08/2026"
    assert sheet["B4"].value == "Guarniciones ñ"
    assert sheet.cell(header + 1, 1).value == "Ensalada de col ácida"
    assert sheet.cell(header + 1, 7).number_format == MONEY_FORMAT
    assert sheet.cell(header + 1, 11).number_format == PERCENT_FORMAT
    assert sheet.freeze_panes == f"A{header + 1}"
    assert sheet.auto_filter.ref.startswith(f"A{header}:")
    assert sheet.cell(header, 1).font.bold
    assert report.totals.sales_amount == Decimal("294")


def test_products_export_preserves_order_salad_totals_and_partial_coverage(export_session) -> None:
    session, context = export_session
    report = build_products_report(
        session,
        date_from="2026-08-01",
        date_to="2026-08-31",
        sort_by="known_profit",
        direction="desc",
        today=TODAY,
    )
    workbook = _workbook(export_products_workbook(report, generated_at=GENERATED))
    sheet = workbook["Productos"]
    header = _header_row(sheet, "Producto")
    salad = _row_by_first_value(sheet, "Ensalada de col ácida", header)
    missing = _row_by_first_value(sheet, "Producto sin costo", header)
    assert salad[2:8] == (15, 6, 9, 294, 150, 144)
    assert missing[5] == 100
    assert missing[6] == missing[7] == "No disponible"
    assert _row_by_first_value(sheet, "Ensalada de col ácida", header) == tuple(sheet[header + 1][index].value for index in range(10))
    summary_values = [sheet.cell(row, 2).value for row in range(1, header)]
    assert PARTIAL_WARNING in summary_values
    assert PENDING_WARNING in summary_values
    assert workbook["Incidencias"]["E2"].value == "Revisar descripción con acento: válido."


def test_branch_export_contains_all_branches_and_exact_salad_values(export_session) -> None:
    session, context = export_session
    report = build_branches_report(
        session,
        date_from="2026-08-12",
        date_to="2026-08-12",
        product=str(context["salad"].id),
        today=TODAY,
    )
    sheet = _workbook(export_branches_workbook(report))["Sucursales"]
    header = _header_row(sheet, "Sucursal")
    assert sheet.max_row - header == 4
    assert _row_by_first_value(sheet, "Solidaridad", header)[1:7] == (3, 2, 1, 98, 30, 68)
    assert _row_by_first_value(sheet, "Alianza", header)[1:7] == (12, 4, 8, 196, 120, 76)
    assert _row_by_first_value(sheet, "Loma Linda", header)[1:5] == (0, 0, 0, 0)
    assert report.totals.total_units == Decimal("15")
    assert report.financial.known_profit == Decimal("144")


def test_week_and_history_exports_keep_complete_periods_and_empty_semantics(export_session) -> None:
    session, _ = export_session
    weekly = build_weekly_report(session, selected_date="2026-08-12", today=TODAY)
    weekly_book = _workbook(export_weekly_workbook(weekly))
    week_sheet = weekly_book["Semana"]
    week_header = _header_row(week_sheet, "Día")
    assert week_sheet.max_row - week_header == 7
    assert weekly_book.sheetnames[:2] == ["Semana", "Productos"]
    weekly_history = build_weekly_history_report(session, year="2026", today=TODAY)
    weekly_history_sheet = _workbook(export_weekly_history_workbook(weekly_history))["Histórico semanal"]
    weekly_header = _header_row(weekly_history_sheet, "Semana")
    assert weekly_history_sheet.max_row - weekly_header == 53
    monthly = build_monthly_report(session, year="2026", today=TODAY)
    monthly_sheet = _workbook(export_monthly_workbook(monthly))["Histórico mensual"]
    monthly_header = _header_row(monthly_sheet, "Mes")
    assert monthly_sheet.max_row - monthly_header == 12
    january = _row_by_first_value(monthly_sheet, "Enero", monthly_header)
    assert january[2:6] == (0, 0, 0, 0)
    assert january[6:10] == ("No disponible", "No disponible", "No aplica", "No aplica")


def test_product_detail_should_have_exports_four_business_sheets(export_session) -> None:
    session, context = export_session
    report = build_product_detail_report(
        session,
        master_product_id=context["salad"].id,
        date_from="2026-08-01",
        date_to="2026-08-31",
        today=TODAY,
    )
    assert report is not None
    exported = export_product_detail_workbook(report)
    workbook = _workbook(exported)
    assert workbook.sheetnames[:4] == ["Resumen", "Evolución", "Precios", "Costos"]
    assert "Ensalada de col ácida" in exported.filename
    assert workbook["Costos"]["B2"].value == 10


@pytest.mark.parametrize(
    "path",
    [
        "/reports/daily/export?date=2026-08-12",
        "/reports/weekly/export?date=2026-08-12&sort=sales_amount&direction=desc",
        "/reports/weekly-history/export?year=2026&metric=sales_amount",
        "/reports/monthly/export?year=2026&metric=sales_amount",
        "/reports/products/export?date_from=2026-08-01&date_to=2026-08-31&sort=known_profit&direction=desc",
        "/reports/branches/export?date_from=2026-08-01&date_to=2026-08-31&metric=sales_amount",
    ],
)
def test_export_endpoints_download_openable_xlsx(export_client, path) -> None:
    response = export_client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == EXCEL_MEDIA_TYPE
    assert "attachment" in response.headers["content-disposition"]
    assert ".xlsx" in response.headers["content-disposition"]
    assert load_workbook(BytesIO(response.content)).sheetnames


def test_product_detail_endpoint_and_not_found(export_client, export_session) -> None:
    _, context = export_session
    response = export_client.get(
        f"/reports/products/{context['salad'].id}/export?date_from=2026-08-01&date_to=2026-08-31"
    )
    assert response.status_code == 200
    assert load_workbook(BytesIO(response.content)).sheetnames[:4] == ["Resumen", "Evolución", "Precios", "Costos"]
    assert export_client.get("/reports/products/999999/export").status_code == 404


def test_dashboard_report_and_excel_totals_are_consistent(export_session) -> None:
    session, context = export_session
    product_id = str(context["salad"].id)
    dashboard = build_dashboard(
        session,
        period="custom",
        date_from="2026-08-12",
        date_to="2026-08-12",
        product=product_id,
        today=TODAY,
    )
    report = build_products_report(
        session,
        date_from="2026-08-12",
        date_to="2026-08-12",
        today=TODAY,
    )
    sheet = _workbook(export_products_workbook(report))["Productos"]
    header = _header_row(sheet, "Producto")
    excel = _row_by_first_value(sheet, "Ensalada de col ácida", header)
    report_financial = report.product_financial[context["salad"].id].financial
    assert dashboard.totals == report.rows[0].metrics
    assert dashboard.financial == report_financial
    assert excel[2:8] == (15, 6, 9, 294, 150, 144)


def test_report_pages_offer_explicit_excel_button_with_current_filters(export_client) -> None:
    for path, endpoint in (
        ("/reports/daily?date=2026-08-12", "/reports/daily/export"),
        ("/reports/weekly?date=2026-08-12", "/reports/weekly/export"),
        ("/reports/weekly-history?year=2026", "/reports/weekly-history/export"),
        ("/reports/monthly?year=2026", "/reports/monthly/export"),
        ("/reports/products?date_from=2026-08-01&date_to=2026-08-31", "/reports/products/export"),
        ("/reports/branches?date_from=2026-08-01&date_to=2026-08-31", "/reports/branches/export"),
    ):
        text = export_client.get(path).text
        assert "Exportar a Excel" in text
        assert f'formaction="{endpoint}"' in text
