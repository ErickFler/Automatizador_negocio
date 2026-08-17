from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Branch, Category, ImportBatch, MasterProduct, SaleRecord
from app.services.aggregation_service import AggregationFilters, Metrics, aggregate_totals
from app.services.cost_service import EffectiveCost, get_effective_costs
from app.services.raw_import_service import COMPLETED, COMPLETED_WITH_WARNINGS

ZERO = Decimal(0)
HUNDRED = Decimal(100)
FinancialSort = Literal[
    "known_cost", "known_profit", "known_margin_percent", "coverage_percent"
]
ALLOWED_FINANCIAL_SORTS: tuple[FinancialSort, ...] = (
    "known_cost",
    "known_profit",
    "known_margin_percent",
    "coverage_percent",
)


@dataclass(frozen=True, slots=True)
class FinancialMetrics:
    total_sales_amount: Decimal = ZERO
    sales_amount_with_known_cost: Decimal = ZERO
    sales_amount_without_cost: Decimal = ZERO
    known_cost: Decimal = ZERO
    known_profit: Decimal = ZERO
    known_margin_percent: Decimal | None = None
    sales_coverage_percent: Decimal | None = None
    record_coverage_percent: Decimal | None = None
    total_records: int = 0
    records_with_cost: int = 0
    records_without_cost: int = 0
    products_without_cost: int = 0

    @property
    def incomplete(self) -> bool:
        return self.records_without_cost > 0


@dataclass(frozen=True, slots=True)
class ProductProfitability:
    master_product_id: int
    product_name: str
    category_id: int
    category_name: str
    sales_metrics: Metrics
    financial: FinancialMetrics


@dataclass(frozen=True, slots=True)
class BranchProfitability:
    branch_id: int
    branch_name: str
    sales_metrics: Metrics
    financial: FinancialMetrics


@dataclass(frozen=True, slots=True)
class CategoryProfitability:
    category_id: int
    category_name: str
    sales_metrics: Metrics
    financial: FinancialMetrics


@dataclass(frozen=True, slots=True)
class DailyProfitability:
    sales_date: date
    sales_metrics: Metrics
    financial: FinancialMetrics


@dataclass(frozen=True, slots=True)
class PeriodProfitability:
    key: str
    period_start: date
    period_end: date
    sales_metrics: Metrics
    financial: FinancialMetrics


@dataclass(frozen=True, slots=True)
class _FinancialFact:
    sales_date: date
    branch_id: int
    branch_name: str
    master_product_id: int
    product_name: str
    category_id: int
    category_name: str
    total_units: Decimal
    individual_sale_units: Decimal
    combo_units: Decimal
    sales_amount: Decimal


@dataclass(frozen=True, slots=True)
class _ResolvedFact:
    fact: _FinancialFact
    effective_cost: EffectiveCost | None


def profitability_totals(
    session: Session, filters: AggregationFilters | None = None
) -> FinancialMetrics:
    selected = filters or AggregationFilters()
    resolved = _resolved_facts(session, selected)
    metrics = _financial_metrics(resolved)
    official_sales = aggregate_totals(session, selected).sales_amount
    return replace(metrics, total_sales_amount=official_sales)


def profitability_by_product(
    session: Session,
    filters: AggregationFilters | None = None,
    *,
    sort_by: str = "known_profit",
    direction: str = "desc",
) -> list[ProductProfitability]:
    _validate_sort(sort_by, direction)
    grouped: dict[int, list[_ResolvedFact]] = {}
    for row in _resolved_facts(session, filters or AggregationFilters()):
        grouped.setdefault(row.fact.master_product_id, []).append(row)
    result = [
        ProductProfitability(
            master_product_id=product_id,
            product_name=rows[0].fact.product_name,
            category_id=rows[0].fact.category_id,
            category_name=rows[0].fact.category_name,
            sales_metrics=_sales_metrics(rows),
            financial=_financial_metrics(rows),
        )
        for product_id, rows in grouped.items()
    ]
    result.sort(key=lambda row: row.product_name.casefold())
    result.sort(
        key=lambda row: _financial_sort_value(row.financial, sort_by),
        reverse=direction == "desc",
    )
    return result


def profitability_by_branch(
    session: Session, filters: AggregationFilters | None = None
) -> list[BranchProfitability]:
    selected = filters or AggregationFilters()
    grouped: dict[int, list[_ResolvedFact]] = {}
    for row in _resolved_facts(session, selected):
        grouped.setdefault(row.fact.branch_id, []).append(row)
    statement = select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
    if selected.branch_id is not None:
        statement = statement.where(Branch.id == selected.branch_id)
    return [
        BranchProfitability(
            branch_id=branch.id,
            branch_name=branch.name,
            sales_metrics=_sales_metrics(grouped.get(branch.id, [])),
            financial=_financial_metrics(grouped.get(branch.id, [])),
        )
        for branch in session.scalars(statement)
    ]


def profitability_by_category(
    session: Session, filters: AggregationFilters | None = None
) -> list[CategoryProfitability]:
    grouped: dict[int, list[_ResolvedFact]] = {}
    for row in _resolved_facts(session, filters or AggregationFilters()):
        grouped.setdefault(row.fact.category_id, []).append(row)
    return sorted(
        (
            CategoryProfitability(
                category_id=category_id,
                category_name=rows[0].fact.category_name,
                sales_metrics=_sales_metrics(rows),
                financial=_financial_metrics(rows),
            )
            for category_id, rows in grouped.items()
        ),
        key=lambda row: row.category_name.casefold(),
    )


def profitability_daily(
    session: Session, filters: AggregationFilters | None = None
) -> list[DailyProfitability]:
    grouped: dict[date, list[_ResolvedFact]] = {}
    for row in _resolved_facts(session, filters or AggregationFilters()):
        grouped.setdefault(row.fact.sales_date, []).append(row)
    return [
        DailyProfitability(
            sales_date=sales_date,
            sales_metrics=_sales_metrics(rows),
            financial=_financial_metrics(rows),
        )
        for sales_date, rows in sorted(grouped.items())
    ]


def profitability_weekly(
    session: Session, filters: AggregationFilters
) -> list[PeriodProfitability]:
    grouped: dict[tuple[int, int], list[_ResolvedFact]] = {}
    for row in _resolved_facts(session, filters):
        iso = row.fact.sales_date.isocalendar()
        grouped.setdefault((iso.year, iso.week), []).append(row)
    return [
        PeriodProfitability(
            key=f"{iso_year}-W{iso_week:02d}",
            period_start=date.fromisocalendar(iso_year, iso_week, 1),
            period_end=date.fromisocalendar(iso_year, iso_week, 7),
            sales_metrics=_sales_metrics(rows),
            financial=_financial_metrics(rows),
        )
        for (iso_year, iso_week), rows in sorted(grouped.items())
    ]


def profitability_monthly(
    session: Session, filters: AggregationFilters
) -> list[PeriodProfitability]:
    grouped: dict[tuple[int, int], list[_ResolvedFact]] = {}
    for row in _resolved_facts(session, filters):
        grouped.setdefault(
            (row.fact.sales_date.year, row.fact.sales_date.month), []
        ).append(row)
    result = []
    for (year, month), rows in sorted(grouped.items()):
        start = date(year, month, 1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(
            day=1
        ) - timedelta(days=1)
        result.append(
            PeriodProfitability(
                key=f"{year}-{month:02d}",
                period_start=start,
                period_end=end,
                sales_metrics=_sales_metrics(rows),
                financial=_financial_metrics(rows),
            )
        )
    return result


def _resolved_facts(
    session: Session, filters: AggregationFilters
) -> list[_ResolvedFact]:
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
        )
        .join(Branch, Branch.id == SaleRecord.branch_id)
        .join(MasterProduct, MasterProduct.id == SaleRecord.master_product_id)
        .join(Category, Category.id == MasterProduct.category_id)
        .join(ImportBatch, ImportBatch.id == SaleRecord.import_batch_id)
        .where(ImportBatch.status.in_((COMPLETED, COMPLETED_WITH_WARNINGS)))
        .order_by(SaleRecord.sales_date, Branch.id, MasterProduct.id)
    )
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
    facts = [_FinancialFact(*row) for row in session.execute(statement)]
    costs = get_effective_costs(
        session, ((fact.master_product_id, fact.sales_date) for fact in facts)
    )
    return [
        _ResolvedFact(
            fact,
            costs[(fact.master_product_id, fact.sales_date)],
        )
        for fact in facts
    ]


def _financial_metrics(rows: list[_ResolvedFact]) -> FinancialMetrics:
    total_sales = sum((row.fact.sales_amount for row in rows), ZERO)
    known_rows = [row for row in rows if row.effective_cost is not None]
    missing_rows = [row for row in rows if row.effective_cost is None]
    known_sales = sum((row.fact.sales_amount for row in known_rows), ZERO)
    missing_sales = sum((row.fact.sales_amount for row in missing_rows), ZERO)
    known_cost = sum(
        (
            row.fact.total_units * row.effective_cost.cost
            for row in known_rows
            if row.effective_cost is not None
        ),
        ZERO,
    )
    known_profit = known_sales - known_cost
    return FinancialMetrics(
        total_sales_amount=total_sales,
        sales_amount_with_known_cost=known_sales,
        sales_amount_without_cost=missing_sales,
        known_cost=known_cost,
        known_profit=known_profit,
        known_margin_percent=(known_profit / known_sales * HUNDRED)
        if known_sales != 0
        else None,
        sales_coverage_percent=(known_sales / total_sales * HUNDRED)
        if total_sales != 0
        else None,
        record_coverage_percent=(Decimal(len(known_rows)) / Decimal(len(rows)) * HUNDRED)
        if rows
        else None,
        total_records=len(rows),
        records_with_cost=len(known_rows),
        records_without_cost=len(missing_rows),
        products_without_cost=len(
            {row.fact.master_product_id for row in missing_rows}
        ),
    )


def _sales_metrics(rows: list[_ResolvedFact]) -> Metrics:
    return Metrics(
        total_units=sum((row.fact.total_units for row in rows), ZERO),
        individual_sale_units=sum(
            (row.fact.individual_sale_units for row in rows), ZERO
        ),
        combo_units=sum((row.fact.combo_units for row in rows), ZERO),
        sales_amount=sum((row.fact.sales_amount for row in rows), ZERO),
    )


def _financial_sort_value(metrics: FinancialMetrics, sort_by: str) -> Decimal:
    if sort_by == "known_margin_percent":
        return metrics.known_margin_percent or ZERO
    if sort_by == "coverage_percent":
        return metrics.sales_coverage_percent or ZERO
    return getattr(metrics, sort_by)


def _validate_sort(sort_by: str, direction: str) -> None:
    if sort_by not in ALLOWED_FINANCIAL_SORTS:
        raise ValueError("El orden financiero solicitado no está permitido.")
    if direction not in {"asc", "desc"}:
        raise ValueError("La dirección de orden debe ser asc o desc.")
