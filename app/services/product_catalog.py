from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Branch, BranchProduct, Category, MasterProduct
from app.domain.normalization import clean_display_text, normalize_for_comparison


@dataclass(frozen=True, slots=True)
class ProductInspectionRow:
    master_product: MasterProduct
    equivalences_by_branch: dict[str, tuple[BranchProduct, ...]]

    @property
    def total_equivalences(self) -> int:
        return sum(len(items) for items in self.equivalences_by_branch.values())


def get_or_create_category(
    session: Session, *, display_name: str
) -> tuple[Category, bool]:
    clean_name = _require_display_name(display_name, field_name="display_name")
    normalized_name = normalize_for_comparison(clean_name)
    existing = session.scalar(
        select(Category).where(Category.normalized_name == normalized_name)
    )
    if existing is not None:
        return existing, False

    category = Category(
        display_name=clean_name,
        normalized_name=normalized_name,
    )
    session.add(category)
    session.flush()
    return category, True


def create_master_product(
    session: Session,
    *,
    display_name: str,
    category: Category,
) -> MasterProduct:
    clean_name = _require_display_name(display_name, field_name="display_name")
    normalized_name = normalize_for_comparison(clean_name)
    if session.scalar(
        select(MasterProduct.id).where(
            MasterProduct.normalized_name == normalized_name
        )
    ) is not None:
        raise ValueError("Ya existe un producto maestro con ese nombre.")

    product = MasterProduct(
        display_name=clean_name,
        normalized_name=normalized_name,
        category=category,
    )
    session.add(product)
    session.flush()
    return product


def create_branch_product(
    session: Session,
    *,
    branch: Branch,
    external_product_id: str,
    original_product_name: str,
    original_category_name: str | None,
    master_product: MasterProduct,
) -> BranchProduct:
    external_id = external_product_id.strip()
    if not external_id:
        raise ValueError("external_product_id no puede estar vacío.")
    _require_display_name(original_product_name, field_name="original_product_name")
    if session.scalar(
        select(BranchProduct.id).where(
            BranchProduct.branch_id == branch.id,
            BranchProduct.external_product_id == external_id,
        )
    ) is not None:
        raise ValueError(
            "Ese IDProducto ya está relacionado dentro de la sucursal seleccionada."
        )

    branch_product = BranchProduct(
        branch=branch,
        external_product_id=external_id,
        original_product_name=original_product_name,
        normalized_product_name=normalize_for_comparison(original_product_name),
        original_category_name=original_category_name,
        master_product=master_product,
    )
    session.add(branch_product)
    session.flush()
    return branch_product


def list_products_for_inspection(session: Session) -> list[ProductInspectionRow]:
    products = session.scalars(
        select(MasterProduct).order_by(MasterProduct.normalized_name)
    ).all()
    branches = session.scalars(select(Branch).order_by(Branch.id)).all()
    branch_products = session.scalars(
        select(BranchProduct).order_by(BranchProduct.original_product_name)
    ).all()
    equivalence_lookup: dict[tuple[int, int], list[BranchProduct]] = {}
    for branch_product in branch_products:
        equivalence_lookup.setdefault(
            (branch_product.master_product_id, branch_product.branch_id), []
        ).append(branch_product)
    return [
        ProductInspectionRow(
            master_product=product,
            equivalences_by_branch={
                branch.name: tuple(
                    equivalence_lookup.get((product.id, branch.id), [])
                )
                for branch in branches
            },
        )
        for product in products
    ]


def _require_display_name(value: str, *, field_name: str) -> str:
    clean_name = clean_display_text(value)
    if not clean_name:
        raise ValueError(f"{field_name} no puede estar vacío.")
    return clean_name
