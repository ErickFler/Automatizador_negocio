from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.models import Category, ImportBatch, MasterProduct as Product, SaleRecord
from app.domain.normalization import PENDING_CATEGORY_NAME
from app.services.sales_processor import COMPLETED, COMPLETED_WITH_WARNINGS


@dataclass(frozen=True, slots=True)
class ConsolidationFilters:
    sales_date_from: date | None = None
    sales_date_to: date | None = None
    branches: tuple[str, ...] = ()
    category_ids: tuple[int, ...] = ()
    product_ids: tuple[int, ...] = ()
    batch_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.sales_date_from is not None
            and self.sales_date_to is not None
            and self.sales_date_from > self.sales_date_to
        ):
            raise ValueError("sales_date_from no puede ser posterior a sales_date_to")


@dataclass(frozen=True, slots=True)
class ConsolidatedSale:
    sales_date: date
    branch: str
    category: str
    external_product_id: str
    product: str
    registered_quantity: Decimal | None
    real_units: Decimal | None
    combo_units: Decimal | None
    total_amount: Decimal | None


def consolidate_sales(
    session: Session, filters: ConsolidationFilters | None = None
) -> list[ConsolidatedSale]:
    """Agrupa el histórico por fecha, sucursal, categoría y producto."""
    selected_filters = filters or ConsolidationFilters()
    category_name = func.coalesce(Category.name, PENDING_CATEGORY_NAME)

    statement = (
        select(
            SaleRecord.sales_date.label("sales_date"),
            SaleRecord.branch.label("branch"),
            category_name.label("category"),
            Product.external_product_id.label("external_product_id"),
            Product.original_name.label("product"),
            func.sum(SaleRecord.quantity).label("registered_quantity"),
            func.sum(SaleRecord.real_units).label("real_units"),
            func.sum(SaleRecord.combo_units).label("combo_units"),
            func.sum(SaleRecord.total_amount).label("total_amount"),
        )
        .join(Product, Product.id == SaleRecord.product_id)
        .join(ImportBatch, ImportBatch.id == SaleRecord.batch_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(ImportBatch.status.in_((COMPLETED, COMPLETED_WITH_WARNINGS)))
        .group_by(
            SaleRecord.sales_date,
            SaleRecord.branch,
            category_name,
            Product.external_product_id,
            Product.original_name,
        )
        .order_by(
            SaleRecord.sales_date,
            SaleRecord.branch,
            category_name,
            Product.original_name,
        )
    )
    statement = _apply_filters(statement, selected_filters)

    return [
        ConsolidatedSale(
            sales_date=row.sales_date,
            branch=row.branch,
            category=row.category,
            external_product_id=row.external_product_id,
            product=row.product,
            registered_quantity=row.registered_quantity,
            real_units=row.real_units,
            combo_units=row.combo_units,
            total_amount=row.total_amount,
        )
        for row in session.execute(statement)
    ]


def _apply_filters(
    statement: Select[tuple[object, ...]], filters: ConsolidationFilters
) -> Select[tuple[object, ...]]:
    if filters.sales_date_from is not None:
        statement = statement.where(SaleRecord.sales_date >= filters.sales_date_from)
    if filters.sales_date_to is not None:
        statement = statement.where(SaleRecord.sales_date <= filters.sales_date_to)
    if filters.branches:
        normalized_branches = tuple(branch.strip() for branch in filters.branches)
        statement = statement.where(SaleRecord.branch.in_(normalized_branches))
    if filters.category_ids:
        statement = statement.where(Category.id.in_(filters.category_ids))
    if filters.product_ids:
        statement = statement.where(Product.id.in_(filters.product_ids))
    if filters.batch_ids:
        statement = statement.where(ImportBatch.id.in_(filters.batch_ids))
    return statement
