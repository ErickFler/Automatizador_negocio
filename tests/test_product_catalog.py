import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import DEFAULT_BRANCHES
from app.db.database import Base, seed_branches
from app.db.models import Branch, BranchProduct, Category, MasterProduct
from app.services.product_catalog import (
    create_branch_product,
    create_master_product,
    get_or_create_category,
    list_products_for_inspection,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        seed_branches(database_session)
        yield database_session


def _branch(session: Session, name: str) -> Branch:
    return session.scalar(select(Branch).where(Branch.name == name))  # type: ignore[return-value]


def _master_product(session: Session) -> MasterProduct:
    category, _ = get_or_create_category(session, display_name="Bebidas")
    return create_master_product(
        session,
        display_name="Coca Cola 3 L",
        category=category,
    )


def test_category_is_created_with_original_and_normalized_name(
    session: Session,
) -> None:
    category, created = get_or_create_category(
        session, display_name="  BEBIDAS   FRÍAS  "
    )

    assert created is True
    assert category.display_name == "BEBIDAS FRÍAS"
    assert category.normalized_name == "bebidas frías"
    assert category.active is True


def test_normalized_category_is_not_duplicated(session: Session) -> None:
    first, first_created = get_or_create_category(session, display_name="Bebidas")
    second, second_created = get_or_create_category(session, display_name="  BEBIDAS ")

    assert first_created is True
    assert second_created is False
    assert second.id == first.id


def test_master_product_is_created_without_external_id(session: Session) -> None:
    product = _master_product(session)

    assert product.display_name == "Coca Cola 3 L"
    assert product.normalized_name == "coca cola 3 l"
    assert product.active is True
    assert not hasattr(product, "external_product_id")


def test_master_product_points_to_master_category(session: Session) -> None:
    product = _master_product(session)

    assert product.category.display_name == "Bebidas"
    assert product in product.category.master_products


def test_branch_product_keeps_original_and_normalized_values(
    session: Session,
) -> None:
    product = _master_product(session)
    branch_product = create_branch_product(
        session,
        branch=_branch(session, "Alianza"),
        external_product_id=" 347 ",
        original_product_name="  COCA   COLA 3 LT ",
        original_category_name=" REFRESCOS ",
        master_product=product,
    )

    assert branch_product.external_product_id == "347"
    assert branch_product.original_product_name == "  COCA   COLA 3 LT "
    assert branch_product.normalized_product_name == "coca cola 3 lt"
    assert branch_product.original_category_name == " REFRESCOS "


def test_branch_product_points_to_branch_and_master_product(
    session: Session,
) -> None:
    product = _master_product(session)
    branch = _branch(session, "Unidad")
    branch_product = create_branch_product(
        session,
        branch=branch,
        external_product_id="346",
        original_product_name="REFRESCO COCA 3L",
        original_category_name="BEBIDAS",
        master_product=product,
    )

    assert branch_product.branch is branch
    assert branch_product.master_product is product


def test_same_external_id_is_allowed_in_different_branches(
    session: Session,
) -> None:
    product = _master_product(session)
    for branch_name in ("Loma Linda", "Solidaridad"):
        create_branch_product(
            session,
            branch=_branch(session, branch_name),
            external_product_id="344",
            original_product_name="COCA COLA 3 L",
            original_category_name="BEBIDAS",
            master_product=product,
        )

    assert len(session.scalars(select(BranchProduct)).all()) == 2


def test_same_branch_and_external_id_is_rejected_by_service(
    session: Session,
) -> None:
    product = _master_product(session)
    branch = _branch(session, "Loma Linda")
    create_branch_product(
        session,
        branch=branch,
        external_product_id="344",
        original_product_name="COCA COLA 3 L",
        original_category_name="BEBIDAS",
        master_product=product,
    )

    with pytest.raises(ValueError, match="ya está relacionado"):
        create_branch_product(
            session,
            branch=branch,
            external_product_id="344",
            original_product_name="OTRO NOMBRE",
            original_category_name="BEBIDAS",
            master_product=product,
        )


def test_database_constraint_rejects_same_branch_and_external_id(
    session: Session,
) -> None:
    product = _master_product(session)
    branch = _branch(session, "Loma Linda")
    values = {
        "branch": branch,
        "external_product_id": "344",
        "original_product_name": "COCA COLA 3 L",
        "normalized_product_name": "coca cola 3 l",
        "original_category_name": "BEBIDAS",
        "master_product": product,
    }
    session.add(BranchProduct(**values))
    session.flush()
    session.add(BranchProduct(**values))

    with pytest.raises(IntegrityError):
        session.flush()


def test_multiple_branch_products_can_share_one_master_product(
    session: Session,
) -> None:
    product = _master_product(session)
    for number, branch_name in enumerate(DEFAULT_BRANCHES, start=1):
        create_branch_product(
            session,
            branch=_branch(session, branch_name),
            external_product_id=str(300 + number),
            original_product_name=f"COCA COLA PRESENTACIÓN {number}",
            original_category_name="BEBIDAS",
            master_product=product,
        )

    assert len(product.branch_products) == 4
    inspection = list_products_for_inspection(session)
    assert inspection[0].total_equivalences == 4
    assert set(inspection[0].equivalences_by_branch) == set(DEFAULT_BRANCHES)
