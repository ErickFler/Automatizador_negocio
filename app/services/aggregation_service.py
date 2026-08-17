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
    "name", "total_units", "individual_sale_units", "combo_units", "sales_amount"
]

ALLOWED_METRICS: tuple[MetricName, ...] = (
    "total_units",
    "individual_sale_units",
    "combo_units",
    "sales_amount",
)
ALLOWED_PRODUCT_SORTS: tuple[ProductSort, ...] = (
    "name",
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
    return _sum_metrics(_load_facts(session, filters or AggregationFilters()))


def aggregate_daily(
    session: Session,
    filters: AggregationFilters | None = None,
    *,
    by_branch: bool = False,
) -> list[DailyAggregate]:
    grouped: dict[tuple[date, int | None], list[_SaleFact]] = {}
    for fact in _load_facts(session, filters or AggregationFilters()):
        key = (fact.sales_date, fact.branch_id if by_branch else None)
        grouped.setdefault(key, []).append(fact)
    result = []
    for (sales_date, branch_id), facts in sorted(grouped.items()):
        result.append(
            DailyAggregate(
                sales_date=sales_date,
                branch_id=branch_id,
                branch_name=facts[0].branch_name if branch_id is not None else None,
                metrics=_sum_metrics(facts),
            )
        )
    return result


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
    grouped: dict[int, list[_SaleFact]] = {}
    for fact in _load_facts(session, selected):
        year, week = iso_year_and_week(fact.sales_date)
        if year == iso_year:
            grouped.setdefault(week, []).append(fact)
    weeks = range(1, last_week + 1) if complete else sorted(grouped)
    return [
        SeriesPoint(
            key=f"{iso_year}-W{week:02d}",
            label=f"Semana {week:02d}",
            value=metric_value(_sum_metrics(grouped.get(week, [])), metric),
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
    grouped: dict[int, list[_SaleFact]] = {}
    for fact in _load_facts(session, selected):
        if fact.sales_date.year == year:
            grouped.setdefault(fact.sales_date.month, []).append(fact)
    months = range(1, 13) if complete else sorted(grouped)
    labels = (
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    )
    return [
        SeriesPoint(
            key=f"{year}-{month:02d}",
            label=labels[month - 1],
            value=metric_value(_sum_metrics(grouped.get(month, [])), metric),
            period_start=date(year, month, 1),
        )
        for month in months
    ]


def aggregate_by_branch(
    session: Session, filters: AggregationFilters | None = None
) -> list[BranchAggregate]:
    selected = filters or AggregationFilters()
    facts = _load_facts(session, selected)
    grouped: dict[int, list[_SaleFact]] = {}
    for fact in facts:
        grouped.setdefault(fact.branch_id, []).append(fact)
    statement = select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
    if selected.branch_id is not None:
        statement = statement.where(Branch.id == selected.branch_id)
    return [
        BranchAggregate(
            branch_id=branch.id,
            branch_name=branch.name,
            metrics=_sum_metrics(grouped.get(branch.id, [])),
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
    grouped: dict[int, list[_SaleFact]] = {}
    for fact in _load_facts(session, filters or AggregationFilters()):
        grouped.setdefault(fact.master_product_id, []).append(fact)
    rows = [
        ProductAggregate(
            master_product_id=product_id,
            product_name=facts[0].product_name,
            category_id=facts[0].category_id,
            category_name=facts[0].category_name,
            metrics=_sum_metrics(facts),
        )
        for product_id, facts in grouped.items()
    ]
    rows.sort(key=lambda row: row.product_name.casefold())
    if sort_by == "name":
        if direction == "desc":
            rows.reverse()
    else:
        rows.sort(
            key=lambda row: metric_value(row.metrics, sort_by),
            reverse=direction == "desc",
        )
    return rows


def aggregate_by_category(
    session: Session, filters: AggregationFilters | None = None
) -> list[CategoryAggregate]:
    grouped: dict[int, list[_SaleFact]] = {}
    for fact in _load_facts(session, filters or AggregationFilters()):
        grouped.setdefault(fact.category_id, []).append(fact)
    return sorted(
        (
            CategoryAggregate(
                category_id=category_id,
                category_name=facts[0].category_name,
                metrics=_sum_metrics(facts),
            )
            for category_id, facts in grouped.items()
        ),
        key=lambda row: row.category_name.casefold(),
    )


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
