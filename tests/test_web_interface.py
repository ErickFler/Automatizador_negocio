from collections.abc import Generator
from dataclasses import replace
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
    assert "/static/css/app.css?v=" in response.text
    assert "/static/js/system.js?v=" in response.text


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
    assert response.text.count('name="branch_id"') == 4
    assert response.text.count('name="file"') == 4
    assert "Carga diaria" in response.text
    assert "Carga semanal" in response.text
    assert "Procesar día" in response.text
    assert 'action="/imports/weekly"' in response.text
    assert response.text.count("data-weekly-slot") == 28
    assert "No necesita cambiarle el nombre" in response.text


def test_four_branch_files_can_be_uploaded_for_one_date(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branches = session.scalars(select(Branch).where(Branch.active.is_(True))).all()
    assert len(branches) == 4
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86]]
    )

    response = client.post(
        "/imports",
        data={
            "sales_date": "2026-08-10",
            "branch_id": [str(branch.id) for branch in branches],
        },
        files=[
            (
                "file",
                (
                    f"ventas-{branch.name}.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
            for branch in branches
        ],
    )

    assert response.status_code == 200
    assert "Carga de sucursales" in response.text
    assert "4 de 4 archivos fueron recibidos" in response.text
    for branch in branches:
        assert branch.name in response.text
    assert len(session.scalars(select(ImportBatch)).all()) == 4


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
    monkeypatch: pytest.MonkeyPatch,
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
    assert "Procesada con observaciones" in response.text
    assert "1 registro requiere atención" in response.text
    assert "Algunos datos del archivo no pudieron calcularse" in response.text
    assert "PrecioVenta no contiene un número válido" not in response.text
    assert "Detalle técnico" not in response.text
    assert "summary-status-warning" in response.text

    batch = session.scalar(select(ImportBatch))
    assert batch is not None
    monkeypatch.setattr(pages, "settings", replace(pages.settings, debug=True))
    technical_response = client.get(f"/imports/{batch.id}")
    assert "Detalle técnico" in technical_response.text
    assert "PrecioVenta no contiene un número válido" in technical_response.text
    assert "valor recibido: &#39;ERROR&#39;" in technical_response.text
    assert "badge-warning" in technical_response.text


def test_import_result_formats_units_and_sales_for_people(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Alianza"))
    assert branch is not None
    category, _ = get_or_create_category(session, display_name="COMPLEMENTOS")
    master = create_master_product(
        session, display_name="Ensalada", category=category
    )
    create_branch_product(
        session,
        branch=branch,
        external_product_id="129",
        original_product_name="ENSALADA",
        original_category_name="COMPLEMENTOS",
        master_product=master,
    )
    session.commit()
    content = _workbook_bytes(
        [["COMPLEMENTOS", 129, "ENSALADA", 15, 10, 4, 60, 100]]
    )

    response = client.post(
        "/imports",
        data={"sales_date": "2026-08-12", "branch_id": str(branch.id)},
        files={"file": ("ensalada.xlsx", content)},
    )

    assert response.status_code == 200
    assert "Unidades totales</span><strong>15</strong>" in response.text
    assert "Unidades venta individual</span><strong>10</strong>" in response.text
    assert "Unidades combo</span><strong>5</strong>" in response.text
    assert "Venta $</span><strong>$100.00</strong>" in response.text
    assert "summary-money" in response.text
    assert "summary-status-success" in response.text
    assert ".000000" not in response.text


def test_zero_price_is_explained_without_technical_language_to_client(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Unidad"))
    assert branch is not None
    category, _ = get_or_create_category(session, display_name="PREPARACION")
    master = create_master_product(
        session, display_name="Pollo asado", category=category
    )
    create_branch_product(
        session,
        branch=branch,
        external_product_id="101",
        original_product_name="POLLO ASADO",
        original_category_name="PREPARACION",
        master_product=master,
    )
    session.commit()
    content = _workbook_bytes(
        [["PREPARACION", 101, "POLLO ASADO", 15, 0, 38.48, 577.20, 0]]
    )

    response = client.post(
        "/imports",
        data={"sales_date": "2026-08-13", "branch_id": str(branch.id)},
        files={"file": ("preparacion.xlsx", content)},
    )

    assert response.status_code == 200
    assert "1 registro requiere atención" in response.text
    assert "productos sin precio de venta" in response.text
    assert "unidades de venta individual y combo" in response.text
    assert "PrecioVenta es cero" not in response.text


def test_unknown_zero_price_row_does_not_block_valid_sales(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Loma Linda"))
    assert branch is not None
    category, _ = get_or_create_category(session, display_name="BEBIDAS")
    master = create_master_product(session, display_name="Refresco", category=category)
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
        [
            ["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86],
            ["PREPARACION", 359, "MEDIO POLLO ASADO", 2, 0, 20, 40, 0],
        ]
    )

    response = client.post(
        "/imports",
        data={"sales_date": "2026-08-10", "branch_id": str(branch.id)},
        files={"file": ("loma.xlsx", content)},
    )
    batch = session.scalar(select(ImportBatch))

    assert response.status_code == 200
    assert batch is not None
    assert batch.status == "COMPLETED_WITH_WARNINGS"
    assert batch.normalized_rows == 1
    assert batch.skipped_rows == 1
    assert "Confirmar productos" not in response.text


def test_weekly_upload_detects_dates_and_branches_from_filenames(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    content = _workbook_bytes(
        [["PREPARACION", 359, "MEDIO POLLO ASADO", 2, 0, 20, 40, 0]]
    )

    response = client.post(
        "/imports/weekly",
        data={"week_date": "2026-08-12"},
        files=[
            ("files", ("ventas_2026-08-10_lunes_Loma_Linda.xlsx", content)),
            ("files", ("ventas_2026-08-11_martes_Unidad.xlsx", content)),
        ],
    )

    assert response.status_code == 200
    assert "Semana recibida" in response.text
    assert "2" in response.text
    assert "Loma Linda" in response.text
    assert "Unidad" in response.text
    batches = session.scalars(select(ImportBatch).order_by(ImportBatch.sales_date)).all()
    assert [batch.sales_date.isoformat() for batch in batches] == [
        "2026-08-10",
        "2026-08-11",
    ]
    assert all(batch.status == "COMPLETED_WITH_WARNINGS" for batch in batches)


def test_weekly_grid_accepts_original_filename_without_date_or_branch(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Unidad"))
    assert branch is not None
    content = _workbook_bytes(
        [["PREPARACION", 359, "MEDIO POLLO ASADO", 2, 0, 20, 40, 0]]
    )

    response = client.post(
        "/imports/weekly",
        data={
            "week_date": "2026-08-12",
            "slot_date": "2026-08-11",
            "slot_branch_id": str(branch.id),
            "replace_existing": "false",
        },
        files=[
            (
                "files",
                ("reporte-ventas-detallado (26).xlsx UNI (1).xlsx", content),
            )
        ],
    )

    assert response.status_code == 200
    assert "1" in response.text
    assert "Unidad" in response.text
    batch = session.scalar(select(ImportBatch))
    assert batch is not None
    assert batch.sales_date.isoformat() == "2026-08-11"
    assert batch.branch_id == branch.id


def test_existing_day_and_branch_require_explicit_replacement(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    branch = session.scalar(select(Branch).where(Branch.name == "Loma Linda"))
    assert branch is not None
    category, _ = get_or_create_category(session, display_name="BEBIDAS")
    master = create_master_product(session, display_name="Refresco", category=category)
    create_branch_product(
        session,
        branch=branch,
        external_product_id="139",
        original_product_name="REFRESCO",
        original_category_name="BEBIDAS",
        master_product=master,
    )
    session.commit()
    first_content = _workbook_bytes(
        [
            ["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86],
            ["PREPARACION", 359, "MEDIO POLLO ASADO", 2, 0, 20, 40, 0],
        ]
    )
    second_content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 3, 43, 20, 60, 129]]
    )

    client.post(
        "/imports",
        data={"sales_date": "2026-08-10", "branch_id": str(branch.id)},
        files={"file": ("primero.xlsx", first_content)},
    )
    blocked = client.post(
        "/imports",
        data={"sales_date": "2026-08-10", "branch_id": str(branch.id)},
        files={"file": ("segundo.xlsx", second_content)},
    )

    assert blocked.status_code == 200
    assert "Ya existe una carga" in blocked.text
    assert len(session.scalars(select(ImportBatch)).all()) == 1

    replaced = client.post(
        "/imports",
        data={
            "sales_date": "2026-08-10",
            "branch_id": str(branch.id),
            "replace_existing": "true",
        },
        files={"file": ("segundo.xlsx", second_content)},
    )

    assert replaced.status_code == 200
    batches = session.scalars(select(ImportBatch)).all()
    assert len(batches) == 1
    assert batches[0].source_filename == "segundo.xlsx"

    coverage = client.get(
        "/imports/coverage?start_date=2026-08-10&days=1"
    )
    assert coverage.status_code == 200
    assert coverage.json()["slots"][0]["branch_id"] == branch.id


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
