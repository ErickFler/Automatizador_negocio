from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Category, MasterProduct
from app.services.product_catalog import create_master_product, get_or_create_category
from app.services.product_matching import (
    find_product_candidates,
    search_master_products,
)


def _session_with_products() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    category, _ = get_or_create_category(session, display_name="Bebidas")
    for name in (
        "Coca Cola 3 L",
        "Coca Cola 2 L",
        "Coca Cola 600 ml",
        "Agua Natural 1 L",
    ):
        create_master_product(session, display_name=name, category=category)
    session.commit()
    return session


def test_exact_normalized_match_appears_first_without_writing() -> None:
    session = _session_with_products()
    before = session.scalar(select(func.count(MasterProduct.id)))

    candidates = find_product_candidates(
        session,
        original_name="  COCA  COLA 3 L ",
        original_category_name="BEBIDAS",
    )

    assert candidates[0].master_product.display_name == "Coca Cola 3 L"
    assert candidates[0].score == 1.0
    assert session.scalar(select(func.count(MasterProduct.id))) == before
    session.close()


def test_similar_candidates_are_ranked_and_numbers_matter() -> None:
    session = _session_with_products()

    candidates = find_product_candidates(
        session,
        original_name="REFRESCO COCA COLA 3 LTS",
        original_category_name="Bebidas",
    )
    names = [candidate.master_product.display_name for candidate in candidates]

    assert names[0] == "Coca Cola 3 L"
    assert "Coca Cola 2 L" not in names or names.index("Coca Cola 3 L") < names.index(
        "Coca Cola 2 L"
    )
    session.close()


def test_unrelated_product_does_not_generate_absurd_suggestions() -> None:
    session = _session_with_products()

    candidates = find_product_candidates(
        session,
        original_name="SERVICIO A DOMICILIO",
        original_category_name="SERVICIOS",
    )

    assert candidates == []
    session.close()


def test_manual_search_finds_existing_master_product() -> None:
    session = _session_with_products()

    results = search_master_products(session, query="coca cola")

    assert [product.display_name for product in results] == [
        "Coca Cola 2 L",
        "Coca Cola 3 L",
        "Coca Cola 600 ml",
    ]
    assert session.scalar(select(func.count(Category.id))) == 1
    session.close()
