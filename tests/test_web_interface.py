from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db, seed_branches
from app.db.models import Branch
from app.main import app
from app.services.product_catalog import (
    create_branch_product,
    create_master_product,
    get_or_create_category,
)


@pytest.fixture
def web_client() -> Generator[tuple[TestClient, Session], None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)

    def override_database() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_database
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides.clear()
    session.close()


def test_home_only_exposes_stage_one_navigation(
    web_client: tuple[TestClient, Session],
) -> None:
    client, _ = web_client

    response = client.get("/")

    assert response.status_code == 200
    assert "Sistema de Concentrado de Ventas" in response.text
    assert "Ver productos" in response.text
    assert 'action="/uploads"' not in response.text
    assert "Apagar sistema" not in response.text


def test_products_page_shows_master_product_and_equivalences_by_branch(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    category, _ = get_or_create_category(session, display_name="Bebidas")
    product = create_master_product(
        session, display_name="Coca Cola 3 L", category=category
    )
    for branch_name, external_id in (("Loma Linda", "344"), ("Alianza", "347")):
        branch = session.scalar(select(Branch).where(Branch.name == branch_name))
        assert branch is not None
        create_branch_product(
            session,
            branch=branch,
            external_product_id=external_id,
            original_product_name="COCA COLA 3 LT",
            original_category_name="REFRESCOS",
            master_product=product,
        )
    session.commit()

    response = client.get("/products")

    assert response.status_code == 200
    assert "Coca Cola 3 L" in response.text
    assert "Bebidas" in response.text
    assert "Loma Linda" in response.text
    assert "Solidaridad" in response.text
    assert "Alianza" in response.text
    assert "Unidad" in response.text
    assert "Total equivalencias" in response.text
    assert "Activo" in response.text
    assert "IDProducto" not in response.text
