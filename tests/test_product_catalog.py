import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Category, Product
from app.domain.normalization import PENDING_CATEGORY_NAME
from app.services.product_catalog import PRODUCT_DATA_CHANGED, register_product


@pytest.fixture
def session() -> Session:
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    with Session(test_engine) as database_session:
        yield database_session


def test_new_product_and_category_are_created(session: Session) -> None:
    result = register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO PEPSI 2 LTS",
        category_name="BEBIDAS",
    )

    assert result.product_created is True
    assert result.category_created is True
    assert result.warnings == []
    assert result.product.category is not None
    assert result.product.category.name == "BEBIDAS"
    assert session.scalar(select(func.count(Product.id))) == 1


def test_existing_product_with_same_data_is_reused(session: Session) -> None:
    first = register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO PEPSI 2 LTS",
        category_name="BEBIDAS",
    )
    second = register_product(
        session,
        external_product_id="139",
        product_name="  refresco pepsi 2 lts ",
        category_name="bebidas",
    )

    assert second.product.id == first.product.id
    assert second.product_created is False
    assert second.category_created is False
    assert second.warnings == []
    assert session.scalar(select(func.count(Product.id))) == 1


def test_existing_product_with_different_name_generates_warning(
    session: Session,
) -> None:
    original = register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO PEPSI 2 LTS",
        category_name="BEBIDAS",
    )

    result = register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO PEPSI 3 LTS",
        category_name="BEBIDAS",
    )

    assert result.warnings[0].warning_type == PRODUCT_DATA_CHANGED
    assert result.warnings[0].previous_values == {
        "product_name": "REFRESCO PEPSI 2 LTS"
    }
    assert result.warnings[0].new_values == {
        "product_name": "REFRESCO PEPSI 3 LTS"
    }
    assert result.product.original_name == original.product.original_name


def test_existing_product_with_different_category_generates_warning(
    session: Session,
) -> None:
    register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO PEPSI 2 LTS",
        category_name="BEBIDAS",
    )

    result = register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO PEPSI 2 LTS",
        category_name="PROMOCIONES",
    )

    assert result.warnings[0].previous_values == {"category_name": "BEBIDAS"}
    assert result.warnings[0].new_values == {"category_name": "PROMOCIONES"}
    assert result.product.category is not None
    assert result.product.category.name == "BEBIDAS"
    assert session.scalar(select(func.count(Category.id))) == 1


def test_a_second_product_can_create_a_new_category(session: Session) -> None:
    register_product(
        session,
        external_product_id="139",
        product_name="REFRESCO",
        category_name="BEBIDAS",
    )
    result = register_product(
        session,
        external_product_id="140",
        product_name="PAPA GALEANA",
        category_name="ALIMENTOS",
    )

    assert result.category_created is True
    assert session.scalar(select(func.count(Category.id))) == 2


@pytest.mark.parametrize("category_name", [None, "", "  "])
def test_product_without_category_is_marked_pending(
    session: Session, category_name: str | None
) -> None:
    result = register_product(
        session,
        external_product_id="200",
        product_name="PRODUCTO SIN CATEGORÍA",
        category_name=category_name,
    )

    assert result.product.category is not None
    assert result.product.category.name == PENDING_CATEGORY_NAME


def test_empty_product_identifier_is_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="external_product_id"):
        register_product(
            session,
            external_product_id="  ",
            product_name="PRODUCTO",
            category_name="CATEGORÍA",
        )

