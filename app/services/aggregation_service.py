from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

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
from app.services.raw_import_service import COMPLETED, COMPLETED_WITH_WARNINGS

MetricName = Literal[
    "total_units", "individual_sale_units", "combo_units", "sales_amount"
]
SortDirection = Literal["asc", "desc"]
ProductSort = Literal[
    "name", "category", "total_units", "individual_sale_units", "combo_units", "sales_amount"
]

ALLOWED_METRICS: tuple[MetricName, ...] = (
    "total_units",
    "individual_sale_units",
    "combo_units",
    "sales_amount",
)
ALLOWED_PRODUCT_SORTS: tuple[ProductSort, ...] = (
    "name",
    "category",
    *ALLOWED_METRICS,
)
ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class AggregationFilters:
    date_from: date | None = None
    date_to: date | None = None
    branch_id: int | None = None
    master_product_id: int | None = None
    category_id: int | None = None

    def __post_init__(self) -> None:
        for label, value in (("date_from", self.date_from), ("date_to", self.date_to)):
            if value is not None and not isinstance(value, date):
                raise TypeError(f"{label} debe ser una fecha válida.")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from no puede ser posterior a date_to.")


@dataclass(frozen=True, slots=True)
class Metrics:
    total_units: Decimal = ZERO
    individual_sale_units: Decimal = ZERO
    combo_units: Decimal = ZERO
    sales_amount: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class DailyAggregate:
    sales_date: date
    metrics: Metrics
    branch_id: int | None = None
    branch_name: str | None = None


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    key: str
    label: str
    value: Decimal
    period_start: date


@dataclass(frozen=True, slots=True)
class BranchAggregate:
    branch_id: int
    branch_name: str
    metrics: Metrics


@dataclass(frozen=True, slots=True)
class ProductAggregate:
    master_product_id: int
    product_name: str
    category_id: int
    category_name: str
    metrics: Metrics


@dataclass(frozen=True, slots=True)
class ProductBranchAggregate:
    master_product_id: int
    product_name: str
    category_id: int
    category_name: str
    branch_id: int
    branch_name: str
    metrics: Metrics
    observed_sale_prices: tuple[Decimal, ...]


@dataclass(frozen=True, slots=True)
class CategoryAggregate:
    category_id: int
    category_name: str
    metrics: Metrics


@dataclass(frozen=True, slots=True)
class IssuePeriodSummary:
    raw_rows_with_issues: int
    affected_batches: int


@dataclass(frozen=True, slots=True)
class PriceHistoryPoint:
    sales_date: date
    branch_id: int
    branch_name: str
    sale_price: Decimal


@dataclass(frozen=True, slots=True)
class _SaleFact:
    sales_date: date
    branch_id: int
    branch_name: str
    master_product_id: int
    product_name: str
    category_id: int
    category_name: str
    total_units: Decimal | None
    individual_sale_units: Decimal | None
    combo_units: Decimal | None
    sales_amount: Decimal | None
    sale_price: Decimal


def aggregate_totals(
    session: Session, filters: AggregationFilters | None = None
) -> Metrics:
    selected = filters or AggregationFilters()
    statement = _aggregate_statement().select_from(SaleRecord)
    statement = _join_sale_dimensions(statement)
    statement = _apply_sale_filters(statement, selected)
    return _metrics_from_row(session.execute(statement).one())


def aggregate_daily(
    session: Session,
    filters: AggregationFilters | None = None,
    *,
    by_branch: bool = False,
) -> list[DailyAggregate]:
    selected = filters or AggregationFilters()
    dimensions = [SaleRecord.sales_date]
    if by_branch:
        dimensions.extend((Branch.id, Branch.name))
    statement = _aggregate_statement(*dimensions).select_from(SaleRecord)
    statement = _join_sale_dimensions(statement, include_branch=by_branch)
    statement = _apply_sale_filters(statement, selected).group_by(*dimensions)
    rows = session.execute(statement)
    return [
        DailyAggregate(
            sales_date=row[0],
            branch_id=row[1] if by_branch else None,
            branch_name=row[2] if by_branch else None,
            metrics=_metrics_from_row(row, offset=3 if by_branch else 1),
        )
        for row in rows
    ]


def iso_year_and_week(value: date) -> tuple[int, int]:
    iso = value.isocalendar()
    return iso.year, iso.week


def iso_week_bounds(iso_year: int, iso_week: int) -> tuple[date, date]:
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise ValueError("La combinación de año y semana ISO no es válida.") from exc
    return monday, monday + timedelta(days=6)


def weekly_daily_series(
    session: Session,
    *,
    iso_year: int,
    iso_week: int,
    metric: MetricName,
    filters: AggregationFilters | None = None,
) -> list[SeriesPoint]:
    _validate_metric(metric)
    monday, sunday = iso_week_bounds(iso_year, iso_week)
    selected = _bounded_filters(filters, monday, sunday)
    values = {
        row.sales_date: metric_value(row.metrics, metric)
        for row in aggregate_daily(session, selected)
    }
    labels = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
    return [
        SeriesPoint(
            key=current.isoformat(),
            label=labels[offset],
            value=values.get(current, ZERO),
            period_start=current,
        )
        for offset in range(7)
        for current in (monday + timedelta(days=offset),)
    ]


def weekly_series(
    session: Session,
    *,
    iso_year: int,
    metric: MetricName,
    filters: AggregationFilters | None = None,
    complete: bool = False,
) -> list[SeriesPoint]:
    _validate_metric(metric)
    first_monday, _ = iso_week_bounds(iso_year, 1)
    last_week = date(iso_year, 12, 28).isocalendar().week
    _, last_sunday = iso_week_bounds(iso_year, last_week)
    selected = _bounded_filters(filters, first_monday, last_sunday)
    grouped: dict[int, list[Metrics]] = {}
    for row in aggregate_daily(session, selected):
        year, week = iso_year_and_week(row.sales_date)
        if year == iso_year:
            grouped.setdefault(week, []).append(row.metrics)
    weeks = range(1, last_week + 1) if complete else sorted(grouped)
    return [
        SeriesPoint(
            key=f"{iso_year}-W{week:02d}",
            label=f"Semana {week:02d}",
            value=metric_value(_sum_metric_rows(grouped.get(week, [])), metric),
            period_start=date.fromisocalendar(iso_year, week, 1),
        )
        for week in weeks
    ]


def monthly_series(
    session: Session,
    *,
    year: int,
    metric: MetricName,
    filters: AggregationFilters | None = None,
    complete: bool = True,
) -> list[SeriesPoint]:
    _validate_metric(metric)
    selected = _bounded_filters(filters, date(year, 1, 1), date(year, 12, 31))
    grouped: dict[int, list[Metrics]] = {}
    for row in aggregate_daily(session, selected):
        if row.sales_date.year == year:
            grouped.setdefault(row.sales_date.month, []).append(row.metrics)
    months = range(1, 13) if complete else sorted(grouped)
    labels = (
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    )
    return [
        SeriesPoint(
            key=f"{year}-{month:02d}",
            label=labels[month - 1],
            value=metric_value(_sum_metric_rows(grouped.get(month, [])), metric),
            period_start=date(year, month, 1),
        )
        for month in months
    ]


def aggregate_by_branch(
    session: Session, filters: AggregationFilters | None = None
) -> list[BranchAggregate]:
    selected = filters or AggregationFilters()
    aggregate_statement = _aggregate_statement(SaleRecord.branch_id).select_from(
        SaleRecord
    )
    aggregate_statement = _join_sale_dimensions(aggregate_statement)
    aggregate_statement = _apply_sale_filters(
        aggregate_statement, selected
    ).group_by(SaleRecord.branch_id)
    grouped = {
        row[0]: _metrics_from_row(row, offset=1)
        for row in session.execute(aggregate_statement)
    }
    statement = select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
    if selected.branch_id is not None:
        statement = statement.where(Branch.id == selected.branch_id)
    return [
        BranchAggregate(
            branch_id=branch.id,
            branch_name=branch.name,
            metrics=grouped.get(branch.id, Metrics()),
        )
        for branch in session.scalars(statement)
    ]


def aggregate_by_product(
    session: Session,
    filters: AggregationFilters | None = None,
    *,
    sort_by: ProductSort = "name",
    direction: SortDirection = "asc",
) -> list[ProductAggregate]:
    _validate_sort(sort_by, direction)
    selected = filters or AggregationFilters()
    dimensions = (
        MasterProduct.id,
        MasterProduct.display_name,
        Category.id,
        Category.display_name,
    )
    statement = _aggregate_statement(*dimensions).select_from(SaleRecord)
    statement = _join_sale_dimensions(statement)
    statement = _apply_sale_filters(statement, selected).group_by(*dimensions)
    rows = [
        ProductAggregate(
            master_product_id=row[0],
            product_name=row[1],
            category_id=row[2],
            category_name=row[3],
            metrics=_metrics_from_row(row, offset=4),
        )
        for row in session.execute(statement)
    ]
    rows.sort(key=lambda row: row.product_name.casefold())
    if sort_by == "name":
        if direction == "desc":
            rows.reverse()
    elif sort_by == "category":
        rows.sort(
            key=lambda row: (row.category_name.casefold(), row.product_name.casefold()),
            reverse=direction == "desc",
        )
    else:
        rows.sort(
            key=lambda row: metric_value(row.metrics, sort_by),
            reverse=direction == "desc",
        )
    return rows


def aggregate_by_product_branch(
    session: Session, filters: AggregationFilters | None = None
) -> list[ProductBranchAggregate]:
    selected = filters or AggregationFilters()
    dimensions = (
        MasterProduct.id,
        MasterProduct.display_name,
        Category.id,
        Category.display_name,
        Branch.id,
        Branch.name,
    )
    statement = _aggregate_statement(*dimensions).select_from(SaleRecord)
    statement = _join_sale_dimensions(statement, include_branch=True)
    statement = _apply_sale_filters(statement, selected).group_by(*dimensions)
    price_statement = (
        select(SaleRecord.master_product_id, SaleRecord.branch_id, SaleRecord.sale_price)
        .select_from(SaleRecord)
        .distinct()
    )
    price_statement = _join_sale_dimensions(price_statement)
    price_statement = _apply_sale_filters(price_statement, selected)
    prices: dict[tuple[int, int], list[Decimal]] = {}
    for product_id, branch_id, sale_price in session.execute(price_statement):
        prices.setdefault((product_id, branch_id), []).append(sale_price)
    return sorted(
        (
            ProductBranchAggregate(
                master_product_id=row[0],
                product_name=row[1],
                category_id=row[2],
                category_name=row[3],
                branch_id=row[4],
                branch_name=row[5],
                metrics=_metrics_from_row(row, offset=6),
                observed_sale_prices=tuple(sorted(prices.get((row[0], row[4]), []))),
            )
            for row in session.execute(statement)
        ),
        key=lambda row: (row.product_name.casefold(), row.branch_name.casefold()),
    )


def aggregate_by_category(
    session: Session, filters: AggregationFilters | None = None
) -> list[CategoryAggregate]:
    selected = filters or AggregationFilters()
    dimensions = (Category.id, Category.display_name)
    statement = _aggregate_statement(*dimensions).select_from(SaleRecord)
    statement = _join_sale_dimensions(statement)
    statement = _apply_sale_filters(statement, selected).group_by(*dimensions)
    return [
        CategoryAggregate(row[0], row[1], _metrics_from_row(row, offset=2))
        for row in session.execute(statement.order_by(Category.display_name))
    ]


def summarize_issues(
    session: Session, filters: AggregationFilters | None = None
) -> IssuePeriodSummary:
    selected = filters or AggregationFilters()
    statement = (
        select(
            func.count(func.distinct(ProcessingIssue.raw_sale_record_id)),
            func.count(func.distinct(ProcessingIssue.import_batch_id)),
        )
        .join(ImportBatch, ImportBatch.id == ProcessingIssue.import_batch_id)
        .join(RawSaleRecord, RawSaleRecord.id == ProcessingIssue.raw_sale_record_id)
        .where(ProcessingIssue.raw_sale_record_id.is_not(None))
    )
    if selected.master_product_id is not None or selected.category_id is not None:
        statement = statement.join(
            BranchProduct,
            and_(
                BranchProduct.branch_id == ImportBatch.branch_id,
                BranchProduct.external_product_id == RawSaleRecord.external_product_id,
            ),
        ).join(MasterProduct, MasterProduct.id == BranchProduct.master_product_id)
    statement = _apply_issue_filters(statement, selected)
    raw_rows, batches = session.execute(statement).one()
    return IssuePeriodSummary(int(raw_rows or 0), int(batches or 0))


def price_history(
    session: Session,
    *,
    master_product_id: int,
    filters: AggregationFilters | None = None,
    changes_only: bool = True,
) -> list[PriceHistoryPoint]:
    selected = replace(
        filters or AggregationFilters(), master_product_id=master_product_id
    )
    facts = sorted(
        _load_facts(session, selected),
        key=lambda fact: (fact.sales_date, fact.branch_name.casefold(), fact.sale_price),
    )
    points: list[PriceHistoryPoint] = []
    last_price_by_branch: dict[int, Decimal] = {}
    seen: set[tuple[date, int, Decimal]] = set()
    for fact in facts:
        identity = (fact.sales_date, fact.branch_id, fact.sale_price)
        if identity in seen:
            continue
        seen.add(identity)
        if changes_only and last_price_by_branch.get(fact.branch_id) == fact.sale_price:
            continue
        last_price_by_branch[fact.branch_id] = fact.sale_price
        points.append(
            PriceHistoryPoint(
                sales_date=fact.sales_date,
                branch_id=fact.branch_id,
                branch_name=fact.branch_name,
                sale_price=fact.sale_price,
            )
        )
    return points


def metric_value(metrics: Metrics, metric: MetricName) -> Decimal:
    _validate_metric(metric)
    return getattr(metrics, metric)


def _aggregate_statement(*dimensions: object) -> Select:
    return select(
        *dimensions,
        func.coalesce(func.sum(SaleRecord.total_units), 0),
        func.coalesce(func.sum(SaleRecord.individual_sale_units), 0),
        func.coalesce(func.sum(SaleRecord.combo_units), 0),
        func.coalesce(func.sum(SaleRecord.sales_amount), 0),
    )


def _join_sale_dimensions(statement: Select, *, include_branch: bool = False) -> Select:
    statement = (
        statement.join(MasterProduct, MasterProduct.id == SaleRecord.master_product_id)
        .join(Category, Category.id == MasterProduct.category_id)
        .join(ImportBatch, ImportBatch.id == SaleRecord.import_batch_id)
        .where(ImportBatch.status.in_((COMPLETED, COMPLETED_WITH_WARNINGS)))
    )
    if include_branch:
        statement = statement.join(Branch, Branch.id == SaleRecord.branch_id)
    return statement


def _metrics_from_row(row: object, *, offset: int = 0) -> Metrics:
    return Metrics(
        total_units=row[offset] or ZERO,
        individual_sale_units=row[offset + 1] or ZERO,
        combo_units=row[offset + 2] or ZERO,
        sales_amount=row[offset + 3] or ZERO,
    )


def _sum_metric_rows(rows: list[Metrics]) -> Metrics:
    return Metrics(
        total_units=sum((row.total_units for row in rows), ZERO),
        individual_sale_units=sum((row.individual_sale_units for row in rows), ZERO),
        combo_units=sum((row.combo_units for row in rows), ZERO),
        sales_amount=sum((row.sales_amount for row in rows), ZERO),
    )


def _load_facts(session: Session, filters: AggregationFilters) -> list[_SaleFact]:
    statement = (
        select(
            SaleRecord.sales_date,
            Branch.id,
            Branch.name,
            MasterProduct.id,
            MasterProduct.display_name,
            Category.id,
            Category.display_name,
            SaleRecord.total_units,
            SaleRecord.individual_sale_units,
            SaleRecord.combo_units,
            SaleRecord.sales_amount,
            SaleRecord.sale_price,
        )
        .join(Branch, Branch.id == SaleRecord.branch_id)
        .join(MasterProduct, MasterProduct.id == SaleRecord.master_product_id)
        .join(Category, Category.id == MasterProduct.category_id)
        .join(ImportBatch, ImportBatch.id == SaleRecord.import_batch_id)
        .where(ImportBatch.status.in_((COMPLETED, COMPLETED_WITH_WARNINGS)))
        .order_by(SaleRecord.sales_date, Branch.id, MasterProduct.display_name)
    )
    statement = _apply_sale_filters(statement, filters)
    return [_SaleFact(*row) for row in session.execute(statement)]


def _apply_sale_filters(statement: Select, filters: AggregationFilters) -> Select:
    if filters.date_from is not None:
        statement = statement.where(SaleRecord.sales_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(SaleRecord.sales_date <= filters.date_to)
    if filters.branch_id is not None:
        statement = statement.where(SaleRecord.branch_id == filters.branch_id)
    if filters.master_product_id is not None:
        statement = statement.where(
            SaleRecord.master_product_id == filters.master_product_id
        )
    if filters.category_id is not None:
        statement = statement.where(MasterProduct.category_id == filters.category_id)
    return statement


def _apply_issue_filters(statement: Select, filters: AggregationFilters) -> Select:
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
    return statement


def _sum_metrics(facts: list[_SaleFact]) -> Metrics:
    return Metrics(
        total_units=sum(
            (fact.total_units for fact in facts if fact.total_units is not None), ZERO
        ),
        individual_sale_units=sum(
            (
                fact.individual_sale_units
                for fact in facts
                if fact.individual_sale_units is not None
            ),
            ZERO,
        ),
        combo_units=sum(
            (fact.combo_units for fact in facts if fact.combo_units is not None), ZERO
        ),
        sales_amount=sum(
            (fact.sales_amount for fact in facts if fact.sales_amount is not None), ZERO
        ),
    )


def _bounded_filters(
    filters: AggregationFilters | None, date_from: date, date_to: date
) -> AggregationFilters:
    selected = filters or AggregationFilters()
    lower = max(value for value in (selected.date_from, date_from) if value is not None)
    upper = min(value for value in (selected.date_to, date_to) if value is not None)
    if lower > upper:
        raise ValueError("El periodo solicitado no coincide con el rango de filtros.")
    return replace(selected, date_from=lower, date_to=upper)


def _validate_metric(metric: str) -> None:
    if metric not in ALLOWED_METRICS:
        raise ValueError("La métrica solicitada no está permitida.")


def _validate_sort(sort_by: str, direction: str) -> None:
    if sort_by not in ALLOWED_PRODUCT_SORTS:
        raise ValueError("El orden solicitado no está permitido.")
    if direction not in {"asc", "desc"}:
        raise ValueError("La dirección de orden debe ser asc o desc.")
