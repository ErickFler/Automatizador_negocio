from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Branch, Category, ImportBatch, MasterProduct
from app.services.aggregation_service import (
    ALLOWED_METRICS,
    ALLOWED_PRODUCT_SORTS,
    AggregationFilters,
    BranchAggregate,
    IssuePeriodSummary,
    MetricName,
    Metrics,
    ProductAggregate,
    aggregate_by_branch,
    aggregate_by_product,
    aggregate_daily,
    aggregate_totals,
    metric_value,
    monthly_series,
    summarize_issues,
    weekly_daily_series,
)
from app.services.raw_import_service import (
    FAILED,
    PENDING,
    PROCESSING,
    WAITING_FOR_PRODUCT_MAPPING,
)
from app.services.profitability_service import FinancialMetrics, profitability_totals

PERIODS = (
    ("today", "Hoy"),
    ("this_week", "Esta semana"),
    ("previous_week", "Semana anterior"),
    ("this_month", "Este mes"),
    ("previous_month", "Mes anterior"),
    ("this_year", "Este año"),
    ("custom", "Personalizado"),
)
METRIC_LABELS: dict[MetricName, str] = {
    "total_units": "Unidades totales",
    "individual_sale_units": "Unidades venta individual",
    "combo_units": "Unidades combo",
    "sales_amount": "Venta $",
}
SORT_DEFAULT = "sales_amount"
DIRECTION_DEFAULT = "desc"
MONTH_NAMES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


class DashboardInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardSelection:
    period: str
    date_from: date
    date_to: date
    branch_id: int | None
    category_id: int | None
    master_product_id: int | None
    metric: MetricName
    sort_by: str
    direction: str


@dataclass(frozen=True, slots=True)
class ChartDataset:
    labels: tuple[str, ...]
    values: tuple[str, ...]
    metric_label: str

    def as_json(self) -> dict[str, object]:
        return {
            "labels": self.labels,
            "values": self.values,
            "metric_label": self.metric_label,
        }


@dataclass(frozen=True, slots=True)
class DashboardView:
    selection: DashboardSelection
    period_label: str
    totals: Metrics
    financial: FinancialMetrics
    branches: tuple[Branch, ...]
    categories: tuple[Category, ...]
    products: tuple[MasterProduct, ...]
    branch_rows: tuple[BranchAggregate, ...]
    product_rows: tuple[ProductAggregate, ...]
    temporal_chart: ChartDataset
    branch_chart: ChartDataset
    issue_summary: IssuePeriodSummary
    pending_batches: int
    has_sales: bool
    sort_links: dict[str, str]
    input_warning: str | None = None

    @property
    def chart_json(self) -> dict[str, object]:
        return {
            "temporal": self.temporal_chart.as_json(),
            "branches": self.branch_chart.as_json(),
            "is_money": self.selection.metric == "sales_amount",
        }


def build_dashboard(
    session: Session,
    *,
    period: str = "this_month",
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "total_units",
    sort_by: str = SORT_DEFAULT,
    direction: str = DIRECTION_DEFAULT,
    today: date | None = None,
) -> DashboardView:
    current_day = today or date.today()
    warning_parts: list[str] = []
    allowed_periods = {value for value, _ in PERIODS}
    if period not in allowed_periods:
        period = "this_month"
        warning_parts.append("El periodo recibido no era válido; se mostró este mes.")
    start, end = resolve_period(
        period, today=current_day, date_from=date_from, date_to=date_to
    )

    branches = tuple(
        session.scalars(
            select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
        )
    )
    categories = tuple(
        session.scalars(
            select(Category)
            .where(Category.active.is_(True))
            .order_by(Category.display_name)
        )
    )
    branch_id = _validated_id(branch, {item.id for item in branches}, "sucursal", warning_parts)
    category_id = _validated_id(
        category, {item.id for item in categories}, "categoría", warning_parts
    )
    product_statement = select(MasterProduct).where(MasterProduct.active.is_(True))
    if category_id is not None:
        product_statement = product_statement.where(
            MasterProduct.category_id == category_id
        )
    products = tuple(
        session.scalars(product_statement.order_by(MasterProduct.display_name))
    )
    product_id = _validated_id(
        product, {item.id for item in products}, "producto", warning_parts
    )

    if metric not in ALLOWED_METRICS:
        metric = "total_units"
        warning_parts.append("La métrica no era válida; se mostraron unidades totales.")
    if sort_by not in ALLOWED_PRODUCT_SORTS:
        sort_by = SORT_DEFAULT
        direction = DIRECTION_DEFAULT
        warning_parts.append("El orden no era válido; se ordenó por Venta $.")
    if direction not in {"asc", "desc"}:
        direction = DIRECTION_DEFAULT
        warning_parts.append("La dirección de orden no era válida.")

    selection = DashboardSelection(
        period=period,
        date_from=start,
        date_to=end,
        branch_id=branch_id,
        category_id=category_id,
        master_product_id=product_id,
        metric=metric,
        sort_by=sort_by,
        direction=direction,
    )
    filters = AggregationFilters(
        date_from=start,
        date_to=end,
        branch_id=branch_id,
        category_id=category_id,
        master_product_id=product_id,
    )
    totals = aggregate_totals(session, filters)
    branch_rows = tuple(aggregate_by_branch(session, filters))
    product_rows = tuple(
        aggregate_by_product(
            session, filters, sort_by=sort_by, direction=direction
        )
    )
    temporal_chart = _temporal_chart(
        session, selection=selection, filters=filters
    )
    branch_chart = ChartDataset(
        labels=tuple(row.branch_name for row in branch_rows),
        values=tuple(str(metric_value(row.metrics, metric)) for row in branch_rows),
        metric_label=METRIC_LABELS[metric],
    )
    return DashboardView(
        selection=selection,
        period_label=format_period_label(period, start, end),
        totals=totals,
        financial=profitability_totals(session, filters),
        branches=branches,
        categories=categories,
        products=products,
        branch_rows=branch_rows,
        product_rows=product_rows,
        temporal_chart=temporal_chart,
        branch_chart=branch_chart,
        issue_summary=summarize_issues(session, filters),
        pending_batches=_pending_batches(session, start, end, branch_id),
        has_sales=bool(product_rows),
        sort_links=_sort_links(selection),
        input_warning=" ".join(warning_parts) or None,
    )


def resolve_period(
    period: str,
    *,
    today: date,
    date_from: str = "",
    date_to: str = "",
) -> tuple[date, date]:
    if period == "today":
        return today, today
    if period in {"this_week", "previous_week"}:
        monday = today - timedelta(days=today.weekday())
        if period == "previous_week":
            monday -= timedelta(days=7)
        return monday, monday + timedelta(days=6)
    if period in {"this_month", "previous_month"}:
        month_start = today.replace(day=1)
        if period == "previous_month":
            month_start = (month_start - timedelta(days=1)).replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return month_start, next_month - timedelta(days=1)
    if period == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if period == "custom":
        try:
            start = date.fromisoformat(date_from)
            end = date.fromisoformat(date_to)
        except ValueError as exc:
            raise DashboardInputError(
                "Indica una fecha inicial y una fecha final válidas."
            ) from exc
        if start > end:
            raise DashboardInputError(
                "La fecha inicial no puede ser posterior a la fecha final."
            )
        return start, end
    raise DashboardInputError("El periodo solicitado no es válido.")


def format_period_label(period: str, start: date, end: date) -> str:
    if period in {"this_week", "previous_week"}:
        return (
            f"Semana del {start.day} al {end.day} de "
            f"{MONTH_NAMES[end.month - 1]} de {end.year}"
        )
    if period == "today":
        return f"Hoy, {start.strftime('%d/%m/%Y')}"
    if period in {"this_month", "previous_month"}:
        month = MONTH_NAMES[start.month - 1].capitalize()
        return f"{month} de {start.year}"
    if period == "this_year":
        return f"Año {start.year}"
    return f"{start.strftime('%d/%m/%Y')} — {end.strftime('%d/%m/%Y')}"


def with_input_error(view: DashboardView, message: str) -> DashboardView:
    return replace(view, input_warning=message)


def _temporal_chart(
    session: Session,
    *,
    selection: DashboardSelection,
    filters: AggregationFilters,
) -> ChartDataset:
    metric = selection.metric
    if selection.period in {"this_week", "previous_week"}:
        iso = selection.date_from.isocalendar()
        points = weekly_daily_series(
            session,
            iso_year=iso.year,
            iso_week=iso.week,
            metric=metric,
            filters=filters,
        )
        labels = tuple(point.label for point in points)
        values = tuple(str(point.value) for point in points)
    elif selection.period == "this_year":
        points = monthly_series(
            session,
            year=selection.date_from.year,
            metric=metric,
            filters=filters,
        )
        labels = tuple(point.label for point in points)
        values = tuple(str(point.value) for point in points)
    else:
        existing = {
            row.sales_date: metric_value(row.metrics, metric)
            for row in aggregate_daily(session, filters)
        }
        dates = _date_range(selection.date_from, selection.date_to)
        labels = tuple(day.strftime("%d/%m") for day in dates)
        values = tuple(str(existing.get(day, Decimal(0))) for day in dates)
    return ChartDataset(labels=labels, values=values, metric_label=METRIC_LABELS[metric])


def _pending_batches(
    session: Session, start: date, end: date, branch_id: int | None
) -> int:
    statement = select(func.count(ImportBatch.id)).where(
        ImportBatch.sales_date >= start,
        ImportBatch.sales_date <= end,
        ImportBatch.status.in_(
            (PENDING, PROCESSING, WAITING_FOR_PRODUCT_MAPPING, FAILED)
        ),
    )
    if branch_id is not None:
        statement = statement.where(ImportBatch.branch_id == branch_id)
    return int(session.scalar(statement) or 0)


def _validated_id(
    raw: str,
    allowed: set[int],
    label: str,
    warnings: list[str],
) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        warnings.append(f"El filtro de {label} no era válido y se ignoró.")
        return None
    if value not in allowed:
        warnings.append(f"El filtro de {label} no está disponible y se ignoró.")
        return None
    return value


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _sort_links(selection: DashboardSelection) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in ALLOWED_PRODUCT_SORTS:
        if selection.sort_by == field:
            next_direction = "asc" if selection.direction == "desc" else "desc"
        else:
            next_direction = "asc" if field in {"name", "category"} else "desc"
        params = {
            "period": selection.period,
            "date_from": selection.date_from.isoformat(),
            "date_to": selection.date_to.isoformat(),
            "branch": selection.branch_id or "",
            "category": selection.category_id or "",
            "product": selection.master_product_id or "",
            "metric": selection.metric,
            "sort": field,
            "direction": next_direction,
        }
        result[field] = f"/?{urlencode(params)}"
    return result
