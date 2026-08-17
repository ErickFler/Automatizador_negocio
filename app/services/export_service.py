from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import re
from typing import Iterable, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.services.aggregation_service import Metrics
from app.services.profitability_service import FinancialMetrics
from app.services.reporting_service import (
    BranchesReport,
    DailyReport,
    HistoryReport,
    ProductDetailReport,
    ProductsReport,
    ReportContext,
    WeeklyReport,
)

EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
QUANTITY_FORMAT = "#,##0.######"
MONEY_FORMAT = '$#,##0.00'
PERCENT_FORMAT = "0.00%"
DATE_FORMAT = "dd/mm/yyyy"
DATETIME_FORMAT = "dd/mm/yyyy hh:mm"
UNAVAILABLE = "No disponible"
NOT_APPLICABLE = "No aplica"
PARTIAL_WARNING = (
    "La utilidad de este reporte es parcial porque faltan costos oficiales "
    "para parte de las ventas."
)
PENDING_WARNING = (
    "Existen cargas pendientes de homologación o procesamiento que no están "
    "incluidas en este reporte."
)
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|]+')
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="285943")
_SUMMARY_FILL = PatternFill(fill_type="solid", fgColor="EAF2ED")
_WARNING_FILL = PatternFill(fill_type="solid", fgColor="FFF4D6")


@dataclass(frozen=True, slots=True)
class ExcelExport:
    filename: str
    content: bytes


def export_daily_workbook(
    report: DailyReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    workbook, sheet = _new_workbook("Ventas diarias")
    header_row = _write_summary(
        sheet, "Ventas diarias", report.context, report.financial, generated_at
    )
    rows = []
    for row in report.rows:
        finance = report.row_financial[(row.master_product_id, row.branch_id)].financial
        rows.append(
            (
                row.product_name,
                row.category_name,
                row.branch_name,
                *_metric_values(row.metrics),
                ", ".join(_plain_decimal(value) for value in row.observed_sale_prices),
                *_financial_values(finance),
            )
        )
    _write_table(
        sheet,
        header_row,
        (
            "Producto", "Categoría", "Sucursal", "Unidades totales",
            "Unidades venta individual", "Unidades combo", "Venta $",
            "Precio observado", "Costo conocido", "Utilidad conocida", "Margen",
            "Cobertura de costos",
        ),
        rows,
        quantity_columns=(4, 5, 6),
        money_columns=(7, 9, 10),
        percent_columns=(11, 12),
    )
    _add_issues_sheet(workbook, report.context)
    filename = f"Ventas_Diarias_{report.sales_date.isoformat()}.xlsx"
    return _finish(workbook, filename)


def export_weekly_workbook(
    report: WeeklyReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    workbook, sheet = _new_workbook("Semana")
    header_row = _write_summary(
        sheet, "Semana lunes a domingo", report.context, report.financial, generated_at
    )
    day_names = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
    rows = []
    for index, row in enumerate(report.day_rows):
        finance = report.day_financial.get(row.sales_date)
        rows.append(
            (
                day_names[index], row.sales_date, *_metric_values(row.metrics),
                *_financial_values(finance.financial if finance else FinancialMetrics()),
            )
        )
    _write_table(
        sheet,
        header_row,
        (
            "Día", "Fecha", "Unidades totales", "Unidades venta individual",
            "Unidades combo", "Venta $", "Costo conocido", "Utilidad conocida",
            "Margen", "Cobertura de costos",
        ),
        rows,
        date_columns=(2,), quantity_columns=(3, 4, 5), money_columns=(6, 7, 8),
        percent_columns=(9, 10),
    )
    products_sheet = workbook.create_sheet("Productos")
    product_rows = []
    for row in report.product_rows:
        finance = report.product_financial[row.master_product_id].financial
        product_rows.append(
            (row.product_name, row.category_name, *_metric_values(row.metrics), *_financial_values(finance))
        )
    _write_table(
        products_sheet,
        1,
        _product_headers(),
        product_rows,
        quantity_columns=(3, 4, 5), money_columns=(6, 7, 8),
        percent_columns=(9, 10),
    )
    _add_issues_sheet(workbook, report.context)
    start = report.context.filters.date_from
    end = report.context.filters.date_to
    return _finish(workbook, f"Semana_{start}_a_{end}.xlsx")


def export_weekly_history_workbook(
    report: HistoryReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    return _export_history(report, "Histórico semanal", "Historico_Semanal", generated_at)


def export_monthly_workbook(
    report: HistoryReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    return _export_history(report, "Histórico mensual", "Historico_Mensual", generated_at)


def export_products_workbook(
    report: ProductsReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    workbook, sheet = _new_workbook("Productos")
    header_row = _write_summary(
        sheet, "Productos", report.context, report.financial, generated_at
    )
    rows = []
    for row in report.rows:
        finance = report.product_financial[row.master_product_id].financial
        rows.append(
            (row.product_name, row.category_name, *_metric_values(row.metrics), *_financial_values(finance))
        )
    _write_table(
        sheet, header_row, _product_headers(), rows,
        quantity_columns=(3, 4, 5), money_columns=(6, 7, 8), percent_columns=(9, 10),
    )
    _add_issues_sheet(workbook, report.context)
    start, end = report.context.filters.date_from, report.context.filters.date_to
    return _finish(workbook, f"Productos_{start}_a_{end}.xlsx")


def export_branches_workbook(
    report: BranchesReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    workbook, sheet = _new_workbook("Sucursales")
    header_row = _write_summary(
        sheet, "Comparativo de sucursales", report.context, report.financial, generated_at
    )
    rows = []
    for row in report.rows:
        finance = report.branch_financial[row.branch_id].financial
        rows.append((row.branch_name, *_metric_values(row.metrics), *_financial_values(finance)))
    _write_table(
        sheet,
        header_row,
        (
            "Sucursal", "Unidades totales", "Unidades venta individual", "Unidades combo",
            "Venta $", "Costo conocido", "Utilidad conocida", "Margen", "Cobertura de costos",
        ),
        rows,
        quantity_columns=(2, 3, 4), money_columns=(5, 6, 7), percent_columns=(8, 9),
    )
    _add_issues_sheet(workbook, report.context)
    start, end = report.context.filters.date_from, report.context.filters.date_to
    return _finish(workbook, f"Sucursales_{start}_a_{end}.xlsx")


def export_product_detail_workbook(
    report: ProductDetailReport, *, generated_at: datetime | None = None
) -> ExcelExport:
    workbook, summary = _new_workbook("Resumen")
    header_row = _write_summary(
        summary,
        f"Producto: {report.product.display_name}",
        report.context,
        report.financial,
        generated_at,
    )
    _write_table(
        summary,
        header_row,
        (
            "Unidades totales", "Unidades venta individual", "Unidades combo", "Venta $",
            "Costo conocido", "Utilidad conocida", "Margen", "Cobertura de costos",
        ),
        [(*_metric_values(report.totals)[:3], report.totals.sales_amount, *_financial_values(report.financial))],
        quantity_columns=(1, 2, 3), money_columns=(4, 5, 6), percent_columns=(7, 8),
    )
    evolution = workbook.create_sheet("Evolución")
    evolution_rows = []
    for row in report.daily_rows:
        finance = report.daily_financial.get(row.sales_date)
        evolution_rows.append(
            (row.sales_date, *_metric_values(row.metrics), *_financial_values(finance.financial if finance else FinancialMetrics()))
        )
    _write_table(
        evolution, 1,
        ("Fecha", "Unidades totales", "Unidades venta individual", "Unidades combo", "Venta $", "Costo conocido", "Utilidad conocida", "Margen", "Cobertura de costos"),
        evolution_rows, date_columns=(1,), quantity_columns=(2, 3, 4),
        money_columns=(5, 6, 7), percent_columns=(8, 9),
    )
    prices = workbook.create_sheet("Precios")
    _write_table(
        prices, 1, ("Fecha", "Sucursal", "Precio observado"),
        ((row.sales_date, row.branch_name, row.sale_price) for row in report.prices),
        date_columns=(1,), money_columns=(3,),
    )
    costs = workbook.create_sheet("Costos")
    _write_table(
        costs, 1, ("Vigente desde", "Costo oficial", "Fuente"),
        ((row.effective_from, row.cost, row.source_filename or "Registro manual") for row in report.official_costs),
        date_columns=(1,), money_columns=(2,),
    )
    _add_issues_sheet(workbook, report.context)
    start, end = report.context.filters.date_from, report.context.filters.date_to
    product_name = _safe_filename(report.product.display_name)
    return _finish(workbook, f"Producto_{product_name}_{start}_a_{end}.xlsx")


def _export_history(
    report: HistoryReport,
    title: str,
    filename_prefix: str,
    generated_at: datetime | None,
) -> ExcelExport:
    workbook, sheet = _new_workbook(title[:31])
    header_row = _write_summary(sheet, title, report.context, report.financial, generated_at)
    weekly = "semanal" in title.casefold()
    headers = (
        ("Semana", "Periodo") if weekly else ("Mes", "Periodo")
    ) + (
        "Unidades totales", "Unidades venta individual", "Unidades combo", "Venta $",
        "Costo conocido", "Utilidad conocida", "Margen", "Cobertura de costos",
    )
    rows = []
    for row in report.rows:
        label = f"Semana {row.number:02d}" if weekly else row.label
        period = f"{row.period_start.strftime('%d/%m/%Y')} - {row.period_end.strftime('%d/%m/%Y')}"
        rows.append((label, period, *_metric_values(row.metrics), *_financial_values(row.financial)))
    _write_table(
        sheet, header_row, headers, rows,
        quantity_columns=(3, 4, 5), money_columns=(6, 7, 8), percent_columns=(9, 10),
    )
    _add_issues_sheet(workbook, report.context)
    return _finish(workbook, f"{filename_prefix}_{report.year}.xlsx")


def _new_workbook(sheet_name: str) -> tuple[Workbook, Worksheet]:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]
    return workbook, sheet


def _write_summary(
    sheet: Worksheet,
    title: str,
    context: ReportContext,
    financial: FinancialMetrics,
    generated_at: datetime | None,
) -> int:
    generated = generated_at or datetime.now().astimezone()
    values: list[tuple[str, object]] = [
        ("Reporte", title),
        ("Periodo", context.period_label),
        ("Sucursal", context.branch_name),
        ("Categoría", context.category_name),
        ("Producto", context.product_name),
        ("Fecha de generación", generated.replace(tzinfo=None)),
        ("Cobertura por importe", _percent_value(financial.sales_coverage_percent)),
        ("Cobertura por registros", _percent_value(financial.record_coverage_percent)),
    ]
    if financial.incomplete:
        values.append(("Advertencia", PARTIAL_WARNING))
    if context.pending_batches:
        values.append(("Cargas pendientes", PENDING_WARNING))
    for row_number, (label, value) in enumerate(values, start=1):
        sheet.cell(row_number, 1, label).font = Font(bold=True, color="285943")
        sheet.cell(row_number, 1).fill = _SUMMARY_FILL
        sheet.cell(row_number, 2, value)
        if label == "Fecha de generación":
            sheet.cell(row_number, 2).number_format = DATETIME_FORMAT
        if label.startswith("Cobertura") and isinstance(value, Decimal):
            sheet.cell(row_number, 2).number_format = PERCENT_FORMAT
        if label in {"Advertencia", "Cargas pendientes"}:
            sheet.cell(row_number, 1).fill = _WARNING_FILL
            sheet.cell(row_number, 2).fill = _WARNING_FILL
            sheet.cell(row_number, 2).alignment = Alignment(wrap_text=True)
    sheet.column_dimensions["A"].width = 24
    sheet.column_dimensions["B"].width = 85
    return len(values) + 2


def _write_table(
    sheet: Worksheet,
    header_row: int,
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    date_columns: tuple[int, ...] = (),
    quantity_columns: tuple[int, ...] = (),
    money_columns: tuple[int, ...] = (),
    percent_columns: tuple[int, ...] = (),
) -> None:
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(header_row, column, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row_number, values in enumerate(rows, start=header_row + 1):
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row_number, column, _excel_value(value))
            if column in date_columns and isinstance(value, (date, datetime)):
                cell.number_format = DATE_FORMAT
            elif column in quantity_columns and isinstance(value, (int, float, Decimal)):
                cell.number_format = QUANTITY_FORMAT
            elif column in money_columns and isinstance(value, (int, float, Decimal)):
                cell.number_format = MONEY_FORMAT
            elif column in percent_columns and isinstance(value, (int, float, Decimal)):
                cell.number_format = PERCENT_FORMAT
            if column in {*quantity_columns, *money_columns, *percent_columns}:
                cell.alignment = Alignment(horizontal="right")
    last_row = max(sheet.max_row, header_row)
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{last_row}"
    _fit_columns(sheet, len(headers))


def _add_issues_sheet(workbook: Workbook, context: ReportContext) -> None:
    if not context.issues:
        return
    sheet = workbook.create_sheet("Incidencias")
    _write_table(
        sheet,
        1,
        ("Fecha", "Sucursal", "Producto", "Fila de origen", "Problema"),
        (
            (
                issue.sales_date,
                issue.branch_name,
                issue.product_name,
                issue.source_row_number,
                issue.message,
            )
            for issue in context.issues
        ),
        date_columns=(1,), quantity_columns=(4,),
    )


def _fit_columns(sheet: Worksheet, column_count: int) -> None:
    for column in range(1, column_count + 1):
        values = (
            len(str(sheet.cell(row, column).value or ""))
            for row in range(1, sheet.max_row + 1)
        )
        sheet.column_dimensions[get_column_letter(column)].width = min(
            max(max(values, default=0) + 2, 12), 45
        )


def _metric_values(metrics: Metrics) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    return (
        metrics.total_units,
        metrics.individual_sale_units,
        metrics.combo_units,
        metrics.sales_amount,
    )


def _financial_values(
    financial: FinancialMetrics,
) -> tuple[Decimal | str, Decimal | str, Decimal | str, Decimal | str]:
    known = financial.records_with_cost > 0
    return (
        financial.known_cost if known else UNAVAILABLE,
        financial.known_profit if known else UNAVAILABLE,
        _percent_value(financial.known_margin_percent),
        _percent_value(financial.sales_coverage_percent),
    )


def _percent_value(value: Decimal | None) -> Decimal | str:
    return value / Decimal(100) if value is not None else NOT_APPLICABLE


def _product_headers() -> tuple[str, ...]:
    return (
        "Producto", "Categoría", "Unidades totales", "Unidades venta individual",
        "Unidades combo", "Venta $", "Costo conocido", "Utilidad conocida",
        "Margen", "Cobertura de costos",
    )


def _plain_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _excel_value(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _safe_filename(value: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", value).strip(" ._")
    return cleaned or "Reporte"


def _finish(workbook: Workbook, filename: str) -> ExcelExport:
    stream = BytesIO()
    workbook.save(stream)
    safe = _safe_filename(filename)
    if not safe.lower().endswith(".xlsx"):
        safe += ".xlsx"
    return ExcelExport(safe, stream.getvalue())
