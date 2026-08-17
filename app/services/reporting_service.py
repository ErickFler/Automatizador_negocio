from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Branch, BranchProduct, Category, ImportBatch, MasterProduct
from app.services.aggregation_service import (
    ALLOWED_METRICS,
    ALLOWED_PRODUCT_SORTS,
    AggregationFilters,
    BranchAggregate,
    DailyAggregate,
    IssuePeriodSummary,
    MetricName,
    Metrics,
    PriceHistoryPoint,
    ProductAggregate,
    ProductBranchAggregate,
    aggregate_by_branch,
    aggregate_by_product,
    aggregate_by_product_branch,
    aggregate_daily,
    aggregate_totals,
    iso_week_bounds,
    iso_year_and_week,
    monthly_series,
    metric_value,
    price_history,
    summarize_issues,
    weekly_series,
)
from app.services.dashboard_service import METRIC_LABELS, MONTH_NAMES
from app.services.raw_import_service import WAITING_FOR_PRODUCT_MAPPING

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ReportContext:
    filters: AggregationFilters
    branches: tuple[Branch, ...]
    categories: tuple[Category, ...]
    products: tuple[MasterProduct, ...]
    branch_name: str
    category_name: str
    product_name: str
    period_label: str
    issue_summary: IssuePeriodSummary
    pending_batches: int
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class TimeMetricsRow:
    key: str
    label: str
    period_start: date
    period_end: date
    metrics: Metrics
    number: int | None = None


@dataclass(frozen=True, slots=True)
class DailyReport:
    context: ReportContext
    sales_date: date
    totals: Metrics
    rows: tuple[ProductBranchAggregate, ...]


@dataclass(frozen=True, slots=True)
class WeeklyReport:
    context: ReportContext
    anchor_date: date
    totals: Metrics
    day_rows: tuple[DailyAggregate, ...]
    product_rows: tuple[ProductAggregate, ...]
    sort_links: dict[str, str]


@dataclass(frozen=True, slots=True)
class HistoryReport:
    context: ReportContext
    year: int
    metric: MetricName
    rows: tuple[TimeMetricsRow, ...]

    @property
    def has_data(self) -> bool:
        return any(row.metrics != Metrics() for row in self.rows)

    @property
    def chart_json(self) -> dict[str, object]:
        return {
            "temporal": {
                "labels": tuple(row.label for row in self.rows),
                "values": tuple(str(metric_value(row.metrics, self.metric)) for row in self.rows),
                "metric_label": METRIC_LABELS[self.metric],
            },
            "branches": {"labels": (), "values": (), "metric_label": ""},
            "is_money": self.metric == "sales_amount",
        }


@dataclass(frozen=True, slots=True)
class ProductsReport:
    context: ReportContext
    totals: Metrics
    rows: tuple[ProductAggregate, ...]
    sort_by: str
    direction: str


@dataclass(frozen=True, slots=True)
class BranchesReport:
    context: ReportContext
    totals: Metrics
    metric: MetricName
    rows: tuple[BranchAggregate, ...]

    @property
    def has_data(self) -> bool:
        return self.totals != Metrics()

    @property
    def chart_json(self) -> dict[str, object]:
        return {
            "temporal": {"labels": (), "values": (), "metric_label": ""},
            "branches": {
                "labels": tuple(row.branch_name for row in self.rows),
                "values": tuple(str(metric_value(row.metrics, self.metric)) for row in self.rows),
                "metric_label": METRIC_LABELS[self.metric],
            },
            "is_money": self.metric == "sales_amount",
        }


@dataclass(frozen=True, slots=True)
class ProductDetailReport:
    context: ReportContext
    product: MasterProduct
    totals: Metrics
    branches: tuple[BranchAggregate, ...]
    daily_rows: tuple[DailyAggregate, ...]
    prices: tuple[PriceHistoryPoint, ...]


def build_daily_report(
    session: Session,
    *,
    selected_date: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    today: date | None = None,
) -> DailyReport:
    warnings: list[str] = []
    day = _date_value(selected_date, today or date.today(), "fecha", warnings)
    context = _context(session, day, day, branch, category, product, warnings)
    return DailyReport(
        context=context,
        sales_date=day,
        totals=aggregate_totals(session, context.filters),
        rows=tuple(aggregate_by_product_branch(session, context.filters)),
    )


def build_weekly_report(
    session: Session,
    *,
    selected_date: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    sort_by: str = "sales_amount",
    direction: str = "desc",
    today: date | None = None,
) -> WeeklyReport:
    warnings: list[str] = []
    anchor = _date_value(selected_date, today or date.today(), "fecha", warnings)
    iso_year, iso_week = iso_year_and_week(anchor)
    start, end = iso_week_bounds(iso_year, iso_week)
    sort_by, direction = _safe_sort(sort_by, direction, warnings)
    context = _context(session, start, end, branch, category, product, warnings)
    context = replace(context, period_label=_week_label(start, end))
    existing = {row.sales_date: row for row in aggregate_daily(session, context.filters)}
    day_rows = tuple(
        existing.get(current, DailyAggregate(current, Metrics()))
        for current in (start + timedelta(days=offset) for offset in range(7))
    )
    return WeeklyReport(
        context=context,
        anchor_date=anchor,
        totals=aggregate_totals(session, context.filters),
        day_rows=day_rows,
        product_rows=tuple(
            aggregate_by_product(
                session, context.filters, sort_by=sort_by, direction=direction
            )
        ),
        sort_links=_sort_links(
            "/reports/weekly",
            context,
            {"date": anchor.isoformat()},
            sort_by,
            direction,
        ),
    )


def build_weekly_history_report(
    session: Session,
    *,
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    today: date | None = None,
) -> HistoryReport:
    warnings: list[str] = []
    selected_year = _year_value(year, (today or date.today()).year, warnings)
    selected_metric = _safe_metric(metric, warnings)
    last_week = date(selected_year, 12, 28).isocalendar().week
    start, _ = iso_week_bounds(selected_year, 1)
    _, end = iso_week_bounds(selected_year, last_week)
    context = _context(session, start, end, branch, category, product, warnings)
    series = {
        name: weekly_series(
            session,
            iso_year=selected_year,
            metric=name,
            filters=context.filters,
            complete=True,
        )
        for name in ALLOWED_METRICS
    }
    rows = tuple(
        TimeMetricsRow(
            key=point.key,
            label=point.label,
            period_start=point.period_start,
            period_end=point.period_start + timedelta(days=6),
            number=index + 1,
            metrics=Metrics(
                total_units=series["total_units"][index].value,
                individual_sale_units=series["individual_sale_units"][index].value,
                combo_units=series["combo_units"][index].value,
                sales_amount=series["sales_amount"][index].value,
            ),
        )
        for index, point in enumerate(series["total_units"])
    )
    return HistoryReport(context, selected_year, selected_metric, rows)


def build_monthly_report(
    session: Session,
    *,
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    today: date | None = None,
) -> HistoryReport:
    warnings: list[str] = []
    selected_year = _year_value(year, (today or date.today()).year, warnings)
    selected_metric = _safe_metric(metric, warnings)
    start, end = date(selected_year, 1, 1), date(selected_year, 12, 31)
    context = _context(session, start, end, branch, category, product, warnings)
    series = {
        name: monthly_series(
            session, year=selected_year, metric=name, filters=context.filters
        )
        for name in ALLOWED_METRICS
    }
    rows = tuple(
        TimeMetricsRow(
            key=point.key,
            label=point.label,
            period_start=point.period_start,
            period_end=_month_end(point.period_start),
            number=index + 1,
            metrics=Metrics(
                total_units=series["total_units"][index].value,
                individual_sale_units=series["individual_sale_units"][index].value,
                combo_units=series["combo_units"][index].value,
                sales_amount=series["sales_amount"][index].value,
            ),
        )
        for index, point in enumerate(series["total_units"])
    )
    return HistoryReport(context, selected_year, selected_metric, rows)


def build_products_report(
    session: Session,
    *,
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    sort_by: str = "sales_amount",
    direction: str = "desc",
    today: date | None = None,
) -> ProductsReport:
    warnings: list[str] = []
    current = today or date.today()
    start, end = _date_range_values(date_from, date_to, current.replace(day=1), current, warnings)
    sort_by, direction = _safe_sort(sort_by, direction, warnings)
    context = _context(session, start, end, branch, category, "", warnings)
    return ProductsReport(
        context=context,
        totals=aggregate_totals(session, context.filters),
        rows=tuple(
            aggregate_by_product(
                session, context.filters, sort_by=sort_by, direction=direction
            )
        ),
        sort_by=sort_by,
        direction=direction,
    )


def build_branches_report(
    session: Session,
    *,
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    today: date | None = None,
) -> BranchesReport:
    warnings: list[str] = []
    current = today or date.today()
    start, end = _date_range_values(date_from, date_to, current.replace(day=1), current, warnings)
    selected_metric = _safe_metric(metric, warnings)
    context = _context(session, start, end, "", category, product, warnings)
    return BranchesReport(
        context=context,
        totals=aggregate_totals(session, context.filters),
        metric=selected_metric,
        rows=tuple(aggregate_by_branch(session, context.filters)),
    )


def build_product_detail_report(
    session: Session,
    *,
    master_product_id: int,
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    today: date | None = None,
) -> ProductDetailReport | None:
    product = session.scalar(
        select(MasterProduct)
        .where(MasterProduct.id == master_product_id, MasterProduct.active.is_(True))
        .options(
            selectinload(MasterProduct.category),
            selectinload(MasterProduct.branch_products).selectinload(
                BranchProduct.branch
            ),
        )
    )
    if product is None:
        return None
    warnings: list[str] = []
    current = today or date.today()
    start, end = _date_range_values(date_from, date_to, date(current.year, 1, 1), current, warnings)
    context = _context(
        session, start, end, branch, str(product.category_id), str(product.id), warnings
    )
    return ProductDetailReport(
        context=context,
        product=product,
        totals=aggregate_totals(session, context.filters),
        branches=tuple(aggregate_by_branch(session, context.filters)),
        daily_rows=tuple(aggregate_daily(session, context.filters)),
        prices=tuple(
            price_history(
                session,
                master_product_id=product.id,
                filters=context.filters,
                changes_only=True,
            )
        ),
    )


def _context(
    session: Session,
    start: date,
    end: date,
    branch_raw: str,
    category_raw: str,
    product_raw: str,
    warnings: list[str],
) -> ReportContext:
    branches = tuple(
        session.scalars(select(Branch).where(Branch.active.is_(True)).order_by(Branch.id))
    )
    categories = tuple(
        session.scalars(
            select(Category).where(Category.active.is_(True)).order_by(Category.display_name)
        )
    )
    branch_id = _validated_id(branch_raw, branches, "sucursal", warnings)
    category_id = _validated_id(category_raw, categories, "categoría", warnings)
    product_statement = select(MasterProduct).where(MasterProduct.active.is_(True))
    if category_id is not None:
        product_statement = product_statement.where(MasterProduct.category_id == category_id)
    products = tuple(session.scalars(product_statement.order_by(MasterProduct.display_name)))
    product_id = _validated_id(product_raw, products, "producto", warnings)
    filters = AggregationFilters(start, end, branch_id, product_id, category_id)
    return ReportContext(
        filters=filters,
        branches=branches,
        categories=categories,
        products=products,
        branch_name=_selected_name(branches, branch_id, "Todas"),
        category_name=_selected_name(categories, category_id, "Todas", "display_name"),
        product_name=_selected_name(products, product_id, "Todos", "display_name"),
        period_label=f"{start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}",
        issue_summary=summarize_issues(session, filters),
        pending_batches=_pending_batches(session, filters),
        warning=" ".join(warnings) or None,
    )


def _pending_batches(session: Session, filters: AggregationFilters) -> int:
    statement = select(func.count(ImportBatch.id)).where(
        ImportBatch.sales_date >= filters.date_from,
        ImportBatch.sales_date <= filters.date_to,
        ImportBatch.status == WAITING_FOR_PRODUCT_MAPPING,
    )
    if filters.branch_id is not None:
        statement = statement.where(ImportBatch.branch_id == filters.branch_id)
    return int(session.scalar(statement) or 0)


def _validated_id(raw: str, choices: tuple, label: str, warnings: list[str]) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value not in {item.id for item in choices}:
        warnings.append(f"El filtro de {label} no era válido y se ignoró.")
        return None
    return value


def _selected_name(choices: tuple, selected_id: int | None, default: str, attribute: str = "name") -> str:
    for item in choices:
        if item.id == selected_id:
            return str(getattr(item, attribute))
    return default


def _date_value(raw: str, default: date, label: str, warnings: list[str]) -> date:
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        warnings.append(f"La {label} no era válida; se utilizó {default.strftime('%d/%m/%Y')}.")
        return default


def _date_range_values(raw_start: str, raw_end: str, default_start: date, default_end: date, warnings: list[str]) -> tuple[date, date]:
    start = _date_value(raw_start, default_start, "fecha inicial", warnings)
    end = _date_value(raw_end, default_end, "fecha final", warnings)
    if start > end:
        warnings.append("El rango no era válido; se utilizó el periodo predeterminado.")
        return default_start, default_end
    return start, end


def _year_value(raw: str, default: int, warnings: list[str]) -> int:
    try:
        value = int(raw) if raw else default
    except ValueError:
        warnings.append(f"El año no era válido; se utilizó {default}.")
        return default
    if value < 2000 or value > 2100:
        warnings.append(f"El año no era válido; se utilizó {default}.")
        return default
    return value


def _safe_metric(raw: str, warnings: list[str]) -> MetricName:
    if raw not in ALLOWED_METRICS:
        warnings.append("La métrica no era válida; se utilizó Venta $.")
        return "sales_amount"
    return raw  # type: ignore[return-value]


def _safe_sort(sort_by: str, direction: str, warnings: list[str]) -> tuple[str, str]:
    if sort_by not in ALLOWED_PRODUCT_SORTS or direction not in {"asc", "desc"}:
        warnings.append("El orden no era válido; se ordenó por Venta $.")
        return "sales_amount", "desc"
    return sort_by, direction


def _sort_links(path: str, context: ReportContext, extra: dict[str, str], current: str, direction: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in ALLOWED_PRODUCT_SORTS:
        next_direction = "asc" if current != field or direction == "desc" else "desc"
        params = {
            **extra,
            "branch": context.filters.branch_id or "",
            "category": context.filters.category_id or "",
            "product": context.filters.master_product_id or "",
            "sort": field,
            "direction": next_direction,
        }
        result[field] = f"{path}?{urlencode(params)}"
    return result


def _month_end(start: date) -> date:
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _week_label(start: date, end: date) -> str:
    if start.month == end.month and start.year == end.year:
        return f"Semana del {start.day} al {end.day} de {MONTH_NAMES[end.month - 1]} de {end.year}"
    return f"Semana del {start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
