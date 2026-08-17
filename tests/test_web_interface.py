from collections.abc import Generator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db, seed_branches
from app.api import pages
from app.db.models import Branch, ImportBatch
from app.main import app
from app.services.excel_reader import REQUIRED_COLUMNS
from app.services.product_catalog import (
    create_branch_product,
    create_master_product,
    get_or_create_category,
)


@pytest.fixture
def web_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, Session], None, None]:
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
    monkeypatch.setattr(pages, "UPLOAD_DIRECTORY", tmp_path / "uploads")
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides.clear()
    session.close()


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(REQUIRED_COLUMNS))
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_home_exposes_raw_import_navigation(
    web_client: tuple[TestClient, Session],
) -> None:
    client, _ = web_client

    response = client.get("/")

    assert response.status_code == 200
    assert "Control y análisis de ventas" in response.text
    assert "Cargar ventas" in response.text
    assert 'action="/uploads"' not in response.text
    assert "Apagar sistema" in response.text
    assert 'action="/system/shutdown"' in response.text


def test_import_form_lists_only_active_persistent_branches(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    session.add(Branch(name="Sucursal inactiva", active=False))
    session.commit()

    response = client.get("/imports/new")

    assert response.status_code == 200
    for branch_name in ("Loma Linda", "Solidaridad", "Alianza", "Unidad"):
        assert branch_name in response.text
    assert "Sucursal inactiva" not in response.text
    assert 'name="sales_date"' in response.text
    assert 'name="branch_id"' in response.text
    assert 'name="file"' in response.text


def test_valid_upload_shows_result_and_minimal_history(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Loma Linda"))
    assert branch is not None
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86]]
    )

    response = client.post(
        "/imports",
        data={"sales_date": "2026-08-08", "branch_id": str(branch.id)},
        files={
            "file": (
                "ventas.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200
    assert "Archivo procesado" in response.text
    assert "Productos por confirmar" in response.text
    assert "Confirmar productos" in response.text
    assert "Loma Linda" in response.text
    assert "Hash" not in response.text
    assert session.scalar(select(ImportBatch)).status == "WAITING_FOR_PRODUCT_MAPPING"

    history = client.get("/imports")
    assert history.status_code == 200
    assert "ventas.xlsx" in history.text


def test_upload_with_invalid_value_shows_friendly_issue(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Alianza"))
    assert branch is not None
    category, _ = get_or_create_category(session, display_name="BEBIDAS")
    master = create_master_product(
        session, display_name="Refresco", category=category
    )
    create_branch_product(
        session,
        branch=branch,
        external_product_id="139",
        original_product_name="REFRESCO",
        original_category_name="BEBIDAS",
        master_product=master,
    )
    session.commit()
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 2, "ERROR", 20, 40, 86]]
    )

    response = client.post(
        "/imports",
        data={"sales_date": "2026-08-09", "branch_id": str(branch.id)},
        files={"file": ("incidencia.xlsx", content)},
    )

    assert response.status_code == 200
    assert "Completada con incidencias" in response.text
    assert "PrecioVenta no contiene un número válido" in response.text
    assert "valor recibido: &#39;ERROR&#39;" in response.text


def test_mapping_screen_can_confirm_an_existing_product(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Loma Linda"))
    assert branch is not None
    category, _ = get_or_create_category(session, display_name="Bebidas")
    master = create_master_product(
        session, display_name="Coca Cola 3 L", category=category
    )
    session.commit()
    content = _workbook_bytes(
        [["BEBIDAS", 344, "REFRESCO COCA COLA 3 LTS", 1, 50, 20, 50, 50]]
    )
    client.post(
        "/imports",
        data={"sales_date": "2026-08-10", "branch_id": str(branch.id)},
        files={"file": ("loma.xlsx", content)},
    )
    batch = session.scalar(select(ImportBatch))
    assert batch is not None

    mapping_page = client.get(f"/imports/{batch.id}/products")
    response = client.post(
        f"/imports/{batch.id}/products/map",
        data={"external_product_id": "344", "master_product_id": str(master.id)},
    )

    assert mapping_page.status_code == 200
    assert "Confirmar productos nuevos" in mapping_page.text
    assert "Coca Cola 3 L" in mapping_page.text
    assert response.status_code == 200
    assert "Archivo procesado" in response.text
    assert "Ventas procesadas" in response.text
    assert "Unidades totales" in response.text
    assert "Unidades venta individual" in response.text
    assert "Unidades combo" in response.text
    assert "Venta $" in response.text
    assert "Unidades reales" not in response.text
    assert "Unidades cobradas" not in response.text
    assert session.get(ImportBatch, batch.id).status == "COMPLETED"


def test_mapping_screen_can_create_a_new_product(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Unidad"))
    assert branch is not None
    content = _workbook_bytes(
        [["POSTRES", 900, "PASTEL CHOCOLATE", 1, 80, 30, 80, 80]]
    )
    client.post(
        "/imports",
        data={"sales_date": "2026-08-11", "branch_id": str(branch.id)},
        files={"file": ("unidad.xlsx", content)},
    )
    batch = session.scalar(select(ImportBatch))
    assert batch is not None

    response = client.post(
        f"/imports/{batch.id}/products/create",
        data={
            "external_product_id": "900",
            "master_display_name": "Pastel de Chocolate",
            "category_display_name": "Postres",
        },
    )

    assert response.status_code == 200
    assert "Archivo procesado" in response.text
    assert "Ventas procesadas" in response.text
    assert "Unidades venta individual" in response.text
    products = client.get("/products")
    assert "Pastel de Chocolate" in products.text
    assert "PASTEL CHOCOLATE" in products.text


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
