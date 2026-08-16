from dataclasses import dataclass, field
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Category, Product
from app.domain.normalization import (
    normalize_for_comparison,
    prepare_category_name,
)

logger = logging.getLogger(__name__)

PRODUCT_DATA_CHANGED = "PRODUCT_DATA_CHANGED"


@dataclass(frozen=True, slots=True)
class CatalogWarning:
    external_product_id: str
    warning_type: str
    description: str
    previous_values: dict[str, str | None]
    new_values: dict[str, str | None]


@dataclass(slots=True)
class CatalogUpdateResult:
    product: Product
    product_created: bool
    category_created: bool = False
    warnings: list[CatalogWarning] = field(default_factory=list)


def register_product(
    session: Session,
    *,
    external_product_id: str,
    product_name: str,
    category_name: str | None,
) -> CatalogUpdateResult:
    """Busca o crea un producto y detecta cambios sin sobrescribirlos."""
    product_id = external_product_id.strip()
    if not product_id:
        raise ValueError("external_product_id no puede estar vacío")
    if not product_name or not product_name.strip():
        raise ValueError("product_name no puede estar vacío")

    normalized_product_name = normalize_for_comparison(product_name)
    display_category_name, normalized_category_name = prepare_category_name(category_name)

    existing_product = session.scalar(
        select(Product).where(Product.external_product_id == product_id)
    )
    if existing_product is not None:
        return _compare_existing_product(
            existing_product,
            product_name=product_name,
            normalized_product_name=normalized_product_name,
            category_name=display_category_name,
            normalized_category_name=normalized_category_name,
        )

    category, category_created = _get_or_create_category(
        session,
        display_name=display_category_name,
        normalized_name=normalized_category_name,
    )
    product = Product(
        external_product_id=product_id,
        original_name=product_name,
        normalized_name=normalized_product_name,
        category=category,
    )
    session.add(product)
    session.flush()
    logger.info("Producto nuevo agregado al catálogo: %s", product_id)
    return CatalogUpdateResult(
        product=product,
        product_created=True,
        category_created=category_created,
    )


def _get_or_create_category(
    session: Session, *, display_name: str, normalized_name: str
) -> tuple[Category, bool]:
    category = session.scalar(
        select(Category).where(Category.normalized_name == normalized_name)
    )
    if category is not None:
        return category, False

    category = Category(name=display_name, normalized_name=normalized_name)
    session.add(category)
    session.flush()
    return category, True


def _compare_existing_product(
    product: Product,
    *,
    product_name: str,
    normalized_product_name: str,
    category_name: str,
    normalized_category_name: str,
) -> CatalogUpdateResult:
    current_category_name = product.category.name if product.category else None
    current_normalized_category = (
        product.category.normalized_name if product.category else None
    )

    previous_values: dict[str, str | None] = {}
    new_values: dict[str, str | None] = {}
    if product.normalized_name != normalized_product_name:
        previous_values["product_name"] = product.original_name
        new_values["product_name"] = product_name
    if current_normalized_category != normalized_category_name:
        previous_values["category_name"] = current_category_name
        new_values["category_name"] = category_name

    warnings: list[CatalogWarning] = []
    if previous_values:
        changed_fields = ", ".join(previous_values)
        warning = CatalogWarning(
            external_product_id=product.external_product_id,
            warning_type=PRODUCT_DATA_CHANGED,
            description=f"Cambió la información del producto: {changed_fields}",
            previous_values=previous_values,
            new_values=new_values,
        )
        warnings.append(warning)
        logger.warning(
            "Cambio detectado en producto %s: %s",
            product.external_product_id,
            changed_fields,
        )

    return CatalogUpdateResult(
        product=product,
        product_created=False,
        warnings=warnings,
    )

