from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    Branch,
    BranchProduct,
    Category,
    ImportBatch,
    MasterProduct,
    ProcessingIssue,
    ProductCost,
    RawSaleRecord,
)
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
from app.services.profitability_service import (
    ALLOWED_FINANCIAL_SORTS,
    BranchProfitability,
    CategoryProfitability,
    DailyProfitability,
    FinancialMetrics,
    PeriodProfitability,
    ProductBranchProfitability,
    ProductProfitability,
    profitability_by_branch,
    profitability_by_category,
    profitability_by_product_branch,
    profitability_by_product,
    profitability_daily,
    profitability_monthly,
    profitability_totals,
    profitability_weekly,
)

ZERO = Decimal(0)
FINANCIAL_TABLE_FIELDS = (*ALLOWED_FINANCIAL_SORTS,)
DAILY_SORT_FIELDS = (
    "name",
    "category",
    "branch",
    *ALLOWED_METRICS,
    "observed_price",
    *FINANCIAL_TABLE_FIELDS,
)
WEEKLY_DAY_SORT_FIELDS = ("date", *ALLOWED_METRICS, *FINANCIAL_TABLE_FIELDS)
HISTORY_SORT_FIELDS = ("period", *ALLOWED_METRICS, *FINANCIAL_TABLE_FIELDS)
BRANCH_SORT_FIELDS = ("branch", *ALLOWED_METRICS, *FINANCIAL_TABLE_FIELDS)
BRANCH_ALLOWED_SORT_FIELDS = ("position", *BRANCH_SORT_FIELDS)
CATEGORY_SORT_FIELDS = ("category", "sales_amount", *FINANCIAL_TABLE_FIELDS)


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
    issues: tuple["ReportIssue", ...]
    pending_batches: int
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class TimeMetricsRow:
    key: str
    label: str
    period_start: date
    period_end: date
    metrics: Metrics
    financial: FinancialMetrics = FinancialMetrics()
    number: int | None = None


@dataclass(frozen=True, slots=True)
class ReportIssue:
    sales_date: date
    branch_name: str
    product_name: str
    source_row_number: int | None
    message: str


@dataclass(frozen=True, slots=True)
class DailyReport:
    context: ReportContext
    sales_date: date
    totals: Metrics
    rows: tuple[ProductBranchAggregate, ...]
    financial: FinancialMetrics
    row_financial: dict[tuple[int, int], ProductBranchProfitability]
    sort_links: dict[str, str]
    sort_by: str
    direction: str


@dataclass(frozen=True, slots=True)
class WeeklyReport:
    context: ReportContext
    anchor_date: date
    totals: Metrics
    day_rows: tuple[DailyAggregate, ...]
    product_rows: tuple[ProductAggregate, ...]
    sort_links: dict[str, str]
    sort_by: str
    direction: str
    financial: FinancialMetrics
    day_financial: dict[date, DailyProfitability]
    product_financial: dict[int, ProductProfitability]
    day_sort_links: dict[str, str]
    day_sort_by: str
    day_direction: str


@dataclass(frozen=True, slots=True)
class HistoryReport:
    context: ReportContext
    year: int
    metric: MetricName
    rows: tuple[TimeMetricsRow, ...]
    financial: FinancialMetrics
    sort_links: dict[str, str]
    sort_by: str
    direction: str

    @property
    def has_data(self) -> bool:
        return any(row.metrics != Metrics() for row in self.rows)

    @property
    def chart_json(self) -> dict[str, object]:
        chronological_rows = tuple(sorted(self.rows, key=lambda row: row.period_start))
        return {
            "temporal": {
                "labels": tuple(row.label for row in chronological_rows),
                "values": tuple(
                    str(metric_value(row.metrics, self.metric))
                    for row in chronological_rows
                ),
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
    sort_links: dict[str, str]
    financial: FinancialMetrics
    product_financial: dict[int, ProductProfitability]
    category_financial: tuple[CategoryProfitability, ...]
    category_sort_links: dict[str, str]
    category_sort_by: str
    category_direction: str


@dataclass(frozen=True, slots=True)
class BranchesReport:
    context: ReportContext
    totals: Metrics
    metric: MetricName
    rows: tuple[BranchAggregate, ...]
    financial: FinancialMetrics
    branch_financial: dict[int, BranchProfitability]
    sort_links: dict[str, str]
    sort_by: str
    direction: str

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
    financial: FinancialMetrics
    official_costs: tuple[ProductCost, ...]
    daily_financial: dict[date, DailyProfitability]

    @property
    def has_cost_changes(self) -> bool:
        return len(self.official_costs) > 1


def build_daily_report(
    session: Session,
    *,
    selected_date: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    sort_by: str = "name",
    direction: str = "asc",
    today: date | None = None,
) -> DailyReport:
    warnings: list[str] = []
    day = _date_value(selected_date, today or date.today(), "fecha", warnings)
    context = _context(session, day, day, branch, category, product, warnings)
    sort_by, direction = _safe_table_sort(
        sort_by, direction, DAILY_SORT_FIELDS, "name", "asc", warnings
    )
    financial_rows = profitability_by_product_branch(session, context.filters)
    row_financial = {
        (row.master_product_id, row.branch_id): row for row in financial_rows
    }
    rows = tuple(
        sorted(
            aggregate_by_product_branch(session, context.filters),
            key=lambda row: _product_branch_sort_value(row, row_financial, sort_by),
            reverse=direction == "desc",
        )
    )
    return DailyReport(
        context=context,
        sales_date=day,
        totals=aggregate_totals(session, context.filters),
        rows=rows,
        financial=profitability_totals(session, context.filters),
        row_financial=row_financial,
        sort_links=_table_sort_links(
            "/reports/daily",
            context,
            {"date": day.isoformat()},
            sort_by,
            direction,
            fields=DAILY_SORT_FIELDS,
            text_fields={"name", "category", "branch"},
            anchor="daily-table",
        ),
        sort_by=sort_by,
        direction=direction,
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
    day_sort_by: str = "date",
    day_direction: str = "asc",
    today: date | None = None,
) -> WeeklyReport:
    warnings: list[str] = []
    anchor = _date_value(selected_date, today or date.today(), "fecha", warnings)
    iso_year, iso_week = iso_year_and_week(anchor)
    start, end = iso_week_bounds(iso_year, iso_week)
    sort_by, direction = _safe_product_report_sort(sort_by, direction, warnings)
    day_sort_by, day_direction = _safe_table_sort(
        day_sort_by,
        day_direction,
        WEEKLY_DAY_SORT_FIELDS,
        "date",
        "asc",
        warnings,
    )
    context = _context(session, start, end, branch, category, product, warnings)
    context = replace(context, period_label=_week_label(start, end))
    existing = {row.sales_date: row for row in aggregate_daily(session, context.filters)}
    daily_financial = {
        row.sales_date: row for row in profitability_daily(session, context.filters)
    }
    product_financial_rows = profitability_by_product(session, context.filters)
    day_rows = tuple(
        existing.get(current, DailyAggregate(current, Metrics()))
        for current in (start + timedelta(days=offset) for offset in range(7))
    )
    day_rows = tuple(
        sorted(
            day_rows,
            key=lambda row: _daily_sort_value(row, daily_financial, day_sort_by),
            reverse=day_direction == "desc",
        )
    )
    if sort_by in ALLOWED_PRODUCT_SORTS:
        product_rows = tuple(
            aggregate_by_product(
                session, context.filters, sort_by=sort_by, direction=direction
            )
        )
    else:
        sorted_financial = profitability_by_product(
            session, context.filters, sort_by=sort_by, direction=direction
        )
        products_by_id = {
            row.master_product_id: row
            for row in aggregate_by_product(session, context.filters)
        }
        product_rows = tuple(
            products_by_id[row.master_product_id]
            for row in sorted_financial
            if row.master_product_id in products_by_id
        )
    return WeeklyReport(
        context=context,
        anchor_date=anchor,
        totals=aggregate_totals(session, context.filters),
        day_rows=day_rows,
        product_rows=product_rows,
        sort_links=_table_sort_links(
            "/reports/weekly",
            context,
            {
                "date": anchor.isoformat(),
                "day_sort": day_sort_by,
                "day_direction": day_direction,
            },
            sort_by,
            direction,
            fields=(*ALLOWED_PRODUCT_SORTS, *ALLOWED_FINANCIAL_SORTS),
            text_fields={"name", "category"},
            anchor="weekly-products-table",
        ),
        sort_by=sort_by,
        direction=direction,
        financial=profitability_totals(session, context.filters),
        day_financial=daily_financial,
        product_financial={
            row.master_product_id: row for row in product_financial_rows
        },
        day_sort_links=_table_sort_links(
            "/reports/weekly",
            context,
            {
                "date": anchor.isoformat(),
                "sort": sort_by,
                "direction": direction,
            },
            day_sort_by,
            day_direction,
            fields=WEEKLY_DAY_SORT_FIELDS,
            text_fields=set(),
            sort_param="day_sort",
            direction_param="day_direction",
            anchor="weekly-days-table",
        ),
        day_sort_by=day_sort_by,
        day_direction=day_direction,
    )


def build_weekly_history_report(
    session: Session,
    *,
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    sort_by: str = "period",
    direction: str = "asc",
    today: date | None = None,
) -> HistoryReport:
    warnings: list[str] = []
    selected_year = _year_value(year, (today or date.today()).year, warnings)
    selected_metric = _safe_metric(metric, warnings)
    sort_by, direction = _safe_table_sort(
        sort_by, direction, HISTORY_SORT_FIELDS, "period", "asc", warnings
    )
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
    financial_series = {
        row.key: row for row in profitability_weekly(session, context.filters)
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
            financial=financial_series.get(point.key, PeriodProfitability(
                point.key,
                point.period_start,
                point.period_start + timedelta(days=6),
                Metrics(),
                FinancialMetrics(),
            )).financial,
        )
        for index, point in enumerate(series["total_units"])
    )
    rows = tuple(
        sorted(
            rows,
            key=lambda row: _history_sort_value(row, sort_by),
            reverse=direction == "desc",
        )
    )
    return HistoryReport(
        context=context,
        year=selected_year,
        metric=selected_metric,
        rows=rows,
        financial=profitability_totals(session, context.filters),
        sort_links=_table_sort_links(
            "/reports/weekly-history",
            context,
            {"year": str(selected_year), "metric": selected_metric},
            sort_by,
            direction,
            fields=HISTORY_SORT_FIELDS,
            text_fields=set(),
            anchor="weekly-history-table",
        ),
        sort_by=sort_by,
        direction=direction,
    )


def build_monthly_report(
    session: Session,
    *,
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    sort_by: str = "period",
    direction: str = "asc",
    today: date | None = None,
) -> HistoryReport:
    warnings: list[str] = []
    selected_year = _year_value(year, (today or date.today()).year, warnings)
    selected_metric = _safe_metric(metric, warnings)
    sort_by, direction = _safe_table_sort(
        sort_by, direction, HISTORY_SORT_FIELDS, "period", "asc", warnings
    )
    start, end = date(selected_year, 1, 1), date(selected_year, 12, 31)
    context = _context(session, start, end, branch, category, product, warnings)
    series = {
        name: monthly_series(
            session, year=selected_year, metric=name, filters=context.filters
        )
        for name in ALLOWED_METRICS
    }
    financial_series = {
        row.key: row for row in profitability_monthly(session, context.filters)
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
            financial=financial_series.get(point.key, PeriodProfitability(
                point.key,
                point.period_start,
                _month_end(point.period_start),
                Metrics(),
                FinancialMetrics(),
            )).financial,
        )
        for index, point in enumerate(series["total_units"])
    )
    rows = tuple(
        sorted(
            rows,
            key=lambda row: _history_sort_value(row, sort_by),
            reverse=direction == "desc",
        )
    )
    return HistoryReport(
        context=context,
        year=selected_year,
        metric=selected_metric,
        rows=rows,
        financial=profitability_totals(session, context.filters),
        sort_links=_table_sort_links(
            "/reports/monthly",
            context,
            {"year": str(selected_year), "metric": selected_metric},
            sort_by,
            direction,
            fields=HISTORY_SORT_FIELDS,
            text_fields=set(),
            anchor="monthly-table",
        ),
        sort_by=sort_by,
        direction=direction,
    )


def build_products_report(
    session: Session,
    *,
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    sort_by: str = "sales_amount",
    direction: str = "desc",
    category_sort_by: str = "category",
    category_direction: str = "asc",
    today: date | None = None,
) -> ProductsReport:
    warnings: list[str] = []
    current = today or date.today()
    start, end = _date_range_values(date_from, date_to, current.replace(day=1), current, warnings)
    sort_by, direction = _safe_product_report_sort(sort_by, direction, warnings)
    category_sort_by, category_direction = _safe_table_sort(
        category_sort_by,
        category_direction,
        CATEGORY_SORT_FIELDS,
        "category",
        "asc",
        warnings,
    )
    context = _context(session, start, end, branch, category, "", warnings)
    if sort_by in ALLOWED_PRODUCT_SORTS:
        rows = tuple(
            aggregate_by_product(
                session, context.filters, sort_by=sort_by, direction=direction
            )
        )
        financial_rows = profitability_by_product(session, context.filters)
    else:
        financial_rows = profitability_by_product(
            session, context.filters, sort_by=sort_by, direction=direction
        )
        sales_by_id = {
            row.master_product_id: row
            for row in aggregate_by_product(session, context.filters)
        }
        rows = tuple(
            sales_by_id[row.master_product_id]
            for row in financial_rows
            if row.master_product_id in sales_by_id
        )
    category_rows = tuple(
        sorted(
            profitability_by_category(session, context.filters),
            key=lambda row: _category_sort_value(row, category_sort_by),
            reverse=category_direction == "desc",
        )
    )
    return ProductsReport(
        context=context,
        totals=aggregate_totals(session, context.filters),
        rows=rows,
        sort_by=sort_by,
        direction=direction,
        sort_links=_table_sort_links(
            "/reports/products",
            context,
            {
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "category_sort": category_sort_by,
                "category_direction": category_direction,
            },
            sort_by,
            direction,
            fields=(*ALLOWED_PRODUCT_SORTS, *ALLOWED_FINANCIAL_SORTS),
            text_fields={"name", "category"},
            anchor="products-table",
        ),
        financial=profitability_totals(session, context.filters),
        product_financial={row.master_product_id: row for row in financial_rows},
        category_financial=category_rows,
        category_sort_links=_table_sort_links(
            "/reports/products",
            context,
            {
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "sort": sort_by,
                "direction": direction,
            },
            category_sort_by,
            category_direction,
            fields=CATEGORY_SORT_FIELDS,
            text_fields={"category"},
            sort_param="category_sort",
            direction_param="category_direction",
            anchor="categories-table",
        ),
        category_sort_by=category_sort_by,
        category_direction=category_direction,
    )


def build_branches_report(
    session: Session,
    *,
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    sort_by: str = "position",
    direction: str = "asc",
    today: date | None = None,
) -> BranchesReport:
    warnings: list[str] = []
    current = today or date.today()
    start, end = _date_range_values(date_from, date_to, current.replace(day=1), current, warnings)
    selected_metric = _safe_metric(metric, warnings)
    sort_by, direction = _safe_table_sort(
        sort_by, direction, BRANCH_ALLOWED_SORT_FIELDS, "position", "asc", warnings
    )
    context = _context(session, start, end, "", category, product, warnings)
    branch_financial_rows = profitability_by_branch(session, context.filters)
    branch_financial = {row.branch_id: row for row in branch_financial_rows}
    rows = tuple(
        sorted(
            aggregate_by_branch(session, context.filters),
            key=lambda row: _branch_sort_value(row, branch_financial, sort_by),
            reverse=direction == "desc",
        )
    )
    return BranchesReport(
        context=context,
        totals=aggregate_totals(session, context.filters),
        metric=selected_metric,
        rows=rows,
        financial=profitability_totals(session, context.filters),
        branch_financial=branch_financial,
        sort_links=_table_sort_links(
            "/reports/branches",
            context,
            {
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "metric": selected_metric,
            },
            sort_by,
            direction,
            fields=BRANCH_SORT_FIELDS,
            text_fields={"branch"},
            anchor="branches-table",
        ),
        sort_by=sort_by,
        direction=direction,
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
    costs = list(
        session.scalars(
            select(ProductCost)
            .where(
                ProductCost.master_product_id == product.id,
                ProductCost.effective_from <= end,
            )
            .order_by(ProductCost.effective_from)
        )
    )
    baseline = next(
        (row for row in reversed(costs) if row.effective_from <= start), None
    )
    official_costs = tuple(
        row
        for row in costs
        if row.effective_from > start or row is baseline
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
        financial=profitability_totals(session, context.filters),
        official_costs=official_costs,
        daily_financial={
            row.sales_date: row
            for row in profitability_daily(session, context.filters)
        },
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
        issues=_report_issues(session, filters),
        pending_batches=_pending_batches(session, filters),
        warning=" ".join(warnings) or None,
    )


def _report_issues(
    session: Session, filters: AggregationFilters
) -> tuple[ReportIssue, ...]:
    statement = (
        select(
            ImportBatch.sales_date,
            Branch.name,
            RawSaleRecord.original_product_name,
            ProcessingIssue.source_row_number,
            ProcessingIssue.message,
        )
        .join(ImportBatch, ImportBatch.id == ProcessingIssue.import_batch_id)
        .join(Branch, Branch.id == ImportBatch.branch_id)
        .outerjoin(RawSaleRecord, RawSaleRecord.id == ProcessingIssue.raw_sale_record_id)
    )
    if filters.master_product_id is not None or filters.category_id is not None:
        statement = statement.join(
            BranchProduct,
            and_(
                BranchProduct.branch_id == ImportBatch.branch_id,
                BranchProduct.external_product_id == RawSaleRecord.external_product_id,
            ),
        ).join(MasterProduct, MasterProduct.id == BranchProduct.master_product_id)
    if filters.date_from is not None:
        statement = statement.where(ImportBatch.sales_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(ImportBatch.sales_date <= filters.date_to)
    if filters.branch_id is not None:
        statement = statement.where(ImportBatch.branch_id == filters.branch_id)
    if filters.master_product_id is not None:
        statement = statement.where(MasterProduct.id == filters.master_product_id)
    if filters.category_id is not None:
        statement = statement.where(MasterProduct.category_id == filters.category_id)
    return tuple(
        ReportIssue(
            sales_date=row.sales_date,
            branch_name=row.name,
            product_name=row.original_product_name or "Sin producto identificado",
            source_row_number=row.source_row_number,
            message=row.message,
        )
        for row in session.execute(
            statement.order_by(
                ImportBatch.sales_date,
                Branch.name,
                ProcessingIssue.source_row_number,
                ProcessingIssue.id,
            )
        )
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


def _safe_product_report_sort(
    sort_by: str, direction: str, warnings: list[str]
) -> tuple[str, str]:
    allowed = {*ALLOWED_PRODUCT_SORTS, *ALLOWED_FINANCIAL_SORTS}
    if sort_by not in allowed or direction not in {"asc", "desc"}:
        warnings.append("El orden no era válido; se ordenó por Venta $.")
        return "sales_amount", "desc"
    return sort_by, direction


def _safe_table_sort(
    sort_by: str,
    direction: str,
    allowed: tuple[str, ...],
    default_sort: str,
    default_direction: str,
    warnings: list[str],
) -> tuple[str, str]:
    if sort_by not in allowed or direction not in {"asc", "desc"}:
        warnings.append("El orden no era válido y se restableció.")
        return default_sort, default_direction
    return sort_by, direction


def _financial_table_sort_value(financial: FinancialMetrics, field: str) -> Decimal:
    if field == "known_margin_percent":
        return financial.known_margin_percent or ZERO
    if field == "coverage_percent":
        return financial.sales_coverage_percent or ZERO
    return getattr(financial, field)


def _product_branch_sort_value(
    row: ProductBranchAggregate,
    financial: dict[tuple[int, int], ProductBranchProfitability],
    field: str,
) -> object:
    if field == "name":
        return row.product_name.casefold()
    if field == "category":
        return row.category_name.casefold()
    if field == "branch":
        return row.branch_name.casefold()
    if field == "observed_price":
        return max(row.observed_sale_prices, default=ZERO)
    if field in ALLOWED_METRICS:
        return getattr(row.metrics, field)
    return _financial_table_sort_value(
        financial[(row.master_product_id, row.branch_id)].financial, field
    )


def _daily_sort_value(
    row: DailyAggregate,
    financial: dict[date, DailyProfitability],
    field: str,
) -> object:
    if field == "date":
        return row.sales_date
    if field in ALLOWED_METRICS:
        return getattr(row.metrics, field)
    finance = financial.get(row.sales_date)
    return _financial_table_sort_value(
        finance.financial if finance else FinancialMetrics(), field
    )


def _history_sort_value(row: TimeMetricsRow, field: str) -> object:
    if field == "period":
        return row.period_start
    if field in ALLOWED_METRICS:
        return getattr(row.metrics, field)
    return _financial_table_sort_value(row.financial, field)


def _category_sort_value(row: CategoryProfitability, field: str) -> object:
    if field == "category":
        return row.category_name.casefold()
    if field == "sales_amount":
        return row.sales_metrics.sales_amount
    return _financial_table_sort_value(row.financial, field)


def _branch_sort_value(
    row: BranchAggregate,
    financial: dict[int, BranchProfitability],
    field: str,
) -> object:
    if field == "position":
        return row.branch_id
    if field == "branch":
        return row.branch_name.casefold()
    if field in ALLOWED_METRICS:
        return getattr(row.metrics, field)
    return _financial_table_sort_value(financial[row.branch_id].financial, field)


def _table_sort_links(
    path: str,
    context: ReportContext,
    extra: dict[str, str],
    current: str,
    direction: str,
    *,
    fields: tuple[str, ...],
    text_fields: set[str],
    sort_param: str = "sort",
    direction_param: str = "direction",
    anchor: str = "",
) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in fields:
        if current == field:
            next_direction = "asc" if direction == "desc" else "desc"
        else:
            next_direction = "asc" if field in text_fields else "desc"
        params = {
            **extra,
            "branch": context.filters.branch_id or "",
            "category": context.filters.category_id or "",
            "product": context.filters.master_product_id or "",
            sort_param: field,
            direction_param: next_direction,
        }
        fragment = f"#{anchor}" if anchor else ""
        result[field] = f"{path}?{urlencode(params)}{fragment}"
    return result


def _month_end(start: date) -> date:
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _week_label(start: date, end: date) -> str:
    if start.month == end.month and start.year == end.year:
        return f"Semana del {start.day} al {end.day} de {MONTH_NAMES[end.month - 1]} de {end.year}"
    return f"Semana del {start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
