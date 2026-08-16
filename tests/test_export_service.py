from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Category, ImportBatch, ProcessingWarning, Product, SaleRecord
from app.services.consolidation_service import ConsolidationFilters
from app.services.export_service import export_sales_workbook


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session


def _prepare_history(session: Session) -> tuple[ImportBatch, ImportBatch, Product]:
    category = Category(name="BEBIDAS", normalized_name="bebidas")
    product = Product(
        external_product_id="139",
        original_name="REFRESCO PEPSI 2 LTS",
        normalized_name="refresco pepsi 2 lts",
        category=category,
    )
    first_batch = ImportBatch(
        sales_date=date(2026, 8, 8),
        branch="Norte",
        source_filename="ventas_norte.xlsx",
        stored_filename="uuid_ventas_norte.xlsx",
        file_sha256="a" * 64,
        status="COMPLETED_WITH_WARNINGS",
        total_rows=1,
        valid_rows=1,
        warning_rows=1,
    )
    second_batch = ImportBatch(
        sales_date=date(2026, 8, 9),
        branch="Sur",
        source_filename="ventas_sur.xlsx",
        stored_filename="uuid_ventas_sur.xlsx",
        file_sha256="b" * 64,
        status="COMPLETED",
        total_rows=1,
        valid_rows=1,
    )
    session.add_all((product, first_batch, second_batch))
    session.flush()
    session.add_all(
        (
            SaleRecord(
                batch=first_batch,
                product=product,
                sales_date=first_batch.sales_date,
                branch=first_batch.branch,
                quantity=Decimal("2"),
                sale_price=Decimal("43"),
                unit_cost=Decimal("20"),
                cost_amount=Decimal("40"),
                total_amount=Decimal("86"),
                real_units=Decimal("2"),
                combo_units=Decimal("0"),
                source_row_number=2,
            ),
            SaleRecord(
                batch=second_batch,
                product=product,
                sales_date=second_batch.sales_date,
                branch=second_batch.branch,
                quantity=Decimal("1"),
                sale_price=Decimal("43"),
                unit_cost=Decimal("20"),
                cost_amount=Decimal("20"),
                total_amount=Decimal("43"),
                real_units=Decimal("1"),
                combo_units=Decimal("0"),
                source_row_number=2,
            ),
            ProcessingWarning(
                batch=first_batch,
                row_number=2,
                product_id="139",
                product_name="REFRESCO PEPSI 2 LTS",
                warning_type="TEST_WARNING",
                description="Advertencia de prueba",
                original_values='{"PrecioVenta": 43}',
            ),
        )
    )
    session.commit()
    return first_batch, second_batch, product


def test_workbook_contains_required_sheets_data_and_format(
    session: Session, tmp_path: Path
) -> None:
    first_batch, _, _ = _prepare_history(session)

    exported = export_sales_workbook(
        session,
        filters=ConsolidationFilters(batch_ids=(first_batch.id,)),
        export_directory=tmp_path,
        filename="concentrado prueba.xlsx",
    )

    workbook = load_workbook(exported)
    assert workbook.sheetnames == [
        "Concentrado",
        "Detalle",
        "Incidencias",
        "Productos Nuevos",
    ]

    concentrate = workbook["Concentrado"]
    assert [cell.value for cell in concentrate[1]] == [
        "Fecha",
        "Sucursal",
        "Categoria",
        "IDProducto",
        "Producto",
        "Cantidad Registrada",
        "Unidades Reales",
        "Unidades Combo",
        "Importe Total",
    ]
    assert concentrate["B2"].value == "Norte"
    assert concentrate["D2"].value == "139"
    assert concentrate["F2"].value == 2
    assert concentrate["G2"].value == 2
    assert concentrate["I2"].value == 86
    assert concentrate["A1"].font.bold is True
    assert concentrate["A2"].number_format == "dd/mm/yyyy"
    assert concentrate["I2"].number_format == '$#,##0.00'
    assert concentrate.freeze_panes == "A2"
    assert concentrate.auto_filter.ref == "A1:I2"

    detail = workbook["Detalle"]
    assert detail["C2"].value == "ventas_norte.xlsx"
    assert detail["D2"].value == first_batch.id
    assert detail["E2"].value == 2
    assert detail["M2"].value == 86

    warnings = workbook["Incidencias"]
    assert warnings["H2"].value == "TEST_WARNING"
    assert warnings["I2"].value == "Advertencia de prueba"

    products = workbook["Productos Nuevos"]
    assert products["A2"].value == first_batch.id
    assert products["D2"].value == "139"
    assert products["F2"].value == "BEBIDAS"


def test_batch_filter_excludes_other_batches(session: Session, tmp_path: Path) -> None:
    _, second_batch, _ = _prepare_history(session)

    exported = export_sales_workbook(
        session,
        filters=ConsolidationFilters(batch_ids=(second_batch.id,)),
        export_directory=tmp_path,
    )

    workbook = load_workbook(exported, read_only=True)
    concentrate = workbook["Concentrado"]
    assert concentrate.max_row == 2
    assert concentrate["B2"].value == "Sur"
    assert workbook["Detalle"]["C2"].value == "ventas_sur.xlsx"
    assert workbook["Incidencias"].max_row == 1
    assert workbook["Productos Nuevos"].max_row == 1


def test_existing_export_is_not_overwritten(session: Session, tmp_path: Path) -> None:
    destination = tmp_path / "existente.xlsx"
    destination.write_bytes(b"archivo previo")

    with pytest.raises(FileExistsError):
        export_sales_workbook(
            session,
            export_directory=tmp_path,
            filename=destination.name,
        )

    assert destination.read_bytes() == b"archivo previo"

