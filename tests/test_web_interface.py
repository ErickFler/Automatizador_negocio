from collections.abc import Generator
from io import BytesIO
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import pages
from app.db.database import Base, get_db
from app.db.models import ImportBatch, Product, SaleRecord
from app.main import app
from app.services.excel_reader import REQUIRED_COLUMNS


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

    def override_database() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override_database
    monkeypatch.setattr(pages, "UPLOAD_DIRECTORY", tmp_path / "uploads")
    monkeypatch.setattr(pages, "EXPORT_DIRECTORY", tmp_path / "exports")
    with TestClient(app) as client:
        yield client, session
    app.dependency_overrides.clear()
    session.close()


def _workbook_bytes(
    rows: list[list[object]], columns: tuple[str, ...] = REQUIRED_COLUMNS
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(columns))
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _upload(
    client: TestClient,
    content: bytes,
    *,
    filename: str = "ventas norte.xlsx",
    branch: str = "Loma Linda",
) -> Response:
    return client.post(
        "/uploads",
        data={"sales_date": "2026-08-08", "branches": branch},
        files={
            "files": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def test_home_exposes_upload_controls(web_client: tuple[TestClient, Session]) -> None:
    client, _ = web_client

    response = client.get("/")

    assert response.status_code == 200
    assert "Sistema de Concentrado de Ventas" in response.text
    assert 'name="sales_date"' in response.text
    assert 'name="branches"' in response.text
    assert 'name="files"' in response.text
    assert "Agregar otra sucursal" in response.text


def test_upload_processes_persists_and_downloads_export(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO PEPSI 2 LTS", 2, 43, 20, 40, 86]]
    )

    response = _upload(client, content)

    assert response.status_code == 200
    assert "Resultado del procesamiento" in response.text
    assert "Procesado correctamente" in response.text
    assert "Unidades reales" in response.text
    assert session.scalar(select(func.count(ImportBatch.id))) == 1
    assert session.scalar(select(func.count(SaleRecord.id))) == 1
    batch = session.scalar(select(ImportBatch))
    assert batch is not None
    assert batch.source_filename == "ventas norte.xlsx"

    matched_link = re.search(r'href="(/exports/[^"]+\.xlsx)"', response.text)
    assert matched_link is not None
    download = client.get(matched_link.group(1))
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(BytesIO(download.content), read_only=True)
    assert workbook.sheetnames == [
        "Concentrado",
        "Detalle",
        "Incidencias",
        "Productos Nuevos",
    ]


def test_multiple_branches_are_processed_in_one_operation(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    first = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86]]
    )
    second = _workbook_bytes(
        [["ALIMENTOS", 140, "PAPA", 3, 49, 15, 45, 147]]
    )

    response = client.post(
        "/uploads",
        data={
            "sales_date": "2026-08-08",
            "branches": ["Loma Linda", "Solidaridad"],
        },
        files=[
            (
                "files",
                (
                    "norte.xlsx",
                    first,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
            (
                "files",
                (
                    "sur.xlsx",
                    second,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    assert "norte.xlsx" in response.text
    assert "sur.xlsx" in response.text
    assert session.scalar(select(func.count(ImportBatch.id))) == 2
    assert session.scalar(select(func.count(SaleRecord.id))) == 2
    assert session.scalar(select(func.count(Product.id))) == 2


def test_history_and_catalog_show_processed_data(
    web_client: tuple[TestClient, Session],
) -> None:
    client, _ = web_client
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO PEPSI 2 LTS", 2, 43, 20, 40, 86]]
    )
    _upload(client, content)

    history = client.get("/history")
    catalog = client.get("/products")

    assert history.status_code == 200
    assert "ventas norte.xlsx" in history.text
    assert "Loma Linda" in history.text
    assert "Exportar" in history.text
    assert catalog.status_code == 200
    assert "REFRESCO PEPSI 2 LTS" in catalog.text
    assert "BEBIDAS" in catalog.text


def test_duplicate_is_reported_without_second_insert(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86]]
    )
    _upload(client, content)

    duplicate = _upload(client, content)

    assert duplicate.status_code == 200
    assert "mismo archivo ya fue cargado" in duplicate.text
    assert "No procesado" in duplicate.text
    assert session.scalar(select(func.count(ImportBatch.id))) == 1


def test_missing_column_error_is_shown_without_persistence(
    web_client: tuple[TestClient, Session],
) -> None:
    client, session = web_client
    columns = tuple(
        column for column in REQUIRED_COLUMNS if column != "ImporteTotal"
    )
    content = _workbook_bytes(
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40]],
        columns=columns,
    )

    response = _upload(client, content, filename="incompleto.xlsx")

    assert response.status_code == 200
    assert "Faltan columnas obligatorias" in response.text
    assert "ImporteTotal" in response.text
    assert session.scalar(select(func.count(ImportBatch.id))) == 0


def test_unknown_export_returns_404(web_client: tuple[TestClient, Session]) -> None:
    client, _ = web_client

    response = client.get("/exports/no-existe.xlsx")

    assert response.status_code == 404
