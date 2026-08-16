from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Category, ImportBatch, ProcessingWarning, Product, SaleRecord
from app.services.duplicate_service import DuplicateImportError
from app.services.excel_reader import REQUIRED_COLUMNS
from app.services.sales_processor import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    FAILED,
    process_sales_file,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session


def _create_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(REQUIRED_COLUMNS))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def test_complete_file_is_persisted_with_traceability(
    session: Session, tmp_path: Path
) -> None:
    source = tmp_path / "ventas.xlsx"
    uploads = tmp_path / "uploads"
    _create_workbook(
        source,
        [["BEBIDAS", 139, "REFRESCO PEPSI 2 LTS", 2, 43, 20, 40, 86]],
    )

    result = process_sales_file(
        session,
        file_path=source,
        sales_date=date(2026, 8, 8),
        branch="Norte",
        upload_directory=uploads,
    )

    batch = session.get(ImportBatch, result.batch_id)
    sale = session.scalar(select(SaleRecord))
    assert result.status == COMPLETED
    assert result.total_rows == 1
    assert result.valid_rows == 1
    assert result.warning_count == 0
    assert batch is not None
    assert batch.sales_date == date(2026, 8, 8)
    assert batch.branch == "Norte"
    assert (uploads / batch.stored_filename).read_bytes() == source.read_bytes()
    assert sale is not None
    assert sale.source_row_number == 2
    assert sale.real_units == Decimal("2.000000")
    assert sale.combo_units == Decimal("0.000000")
    assert sale.product.external_product_id == "139"


def test_warning_file_is_persisted_without_discarding_valid_product(
    session: Session, tmp_path: Path
) -> None:
    source = tmp_path / "precio_cero.xlsx"
    _create_workbook(
        source,
        [["BEBIDAS", 139, "REFRESCO", 2, 0, 20, 40, 86]],
    )

    result = process_sales_file(
        session,
        file_path=source,
        sales_date=date(2026, 8, 8),
        branch="Norte",
        upload_directory=tmp_path / "uploads",
    )

    warning = session.scalar(select(ProcessingWarning))
    sale = session.scalar(select(SaleRecord))
    assert result.status == COMPLETED_WITH_WARNINGS
    assert result.valid_rows == 1
    assert result.warning_rows == 1
    assert warning is not None
    assert warning.warning_type == "ZERO_SALE_PRICE"
    assert warning.row_number == 2
    assert sale is not None
    assert sale.real_units is None
    assert sale.combo_units is None


def test_row_without_product_identity_is_kept_as_warning_only(
    session: Session, tmp_path: Path
) -> None:
    source = tmp_path / "sin_producto.xlsx"
    _create_workbook(
        source,
        [["BEBIDAS", None, None, 2, 43, 20, 40, 86]],
    )

    result = process_sales_file(
        session,
        file_path=source,
        sales_date=date(2026, 8, 8),
        branch="Norte",
        upload_directory=tmp_path / "uploads",
    )

    assert result.status == COMPLETED_WITH_WARNINGS
    assert result.total_rows == 1
    assert result.valid_rows == 0
    assert session.scalar(select(func.count(SaleRecord.id))) == 0
    assert session.scalar(select(func.count(ProcessingWarning.id))) == 2


def test_whitespace_product_name_does_not_fail_the_batch(
    session: Session, tmp_path: Path
) -> None:
    source = tmp_path / "nombre_vacio.xlsx"
    _create_workbook(
        source,
        [["BEBIDAS", 139, "   ", 2, 43, 20, 40, 86]],
    )

    result = process_sales_file(
        session,
        file_path=source,
        sales_date=date(2026, 8, 8),
        branch="Norte",
        upload_directory=tmp_path / "uploads",
    )

    assert result.status == COMPLETED_WITH_WARNINGS
    assert result.valid_rows == 0
    assert session.scalar(select(func.count(SaleRecord.id))) == 0
    batch = session.scalar(select(ImportBatch))
    assert batch is not None
    assert batch.status != FAILED


def test_exact_duplicate_is_not_inserted_silently(
    session: Session, tmp_path: Path
) -> None:
    source = tmp_path / "ventas.xlsx"
    _create_workbook(
        source,
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86]],
    )
    arguments = {
        "file_path": source,
        "sales_date": date(2026, 8, 8),
        "branch": "Norte",
        "upload_directory": tmp_path / "uploads",
    }
    first = process_sales_file(session, **arguments)

    with pytest.raises(DuplicateImportError) as captured:
        process_sales_file(session, **arguments)

    assert captured.value.existing_batch_id == first.batch_id
    assert session.scalar(select(func.count(ImportBatch.id))) == 1
    assert session.scalar(select(func.count(SaleRecord.id))) == 1
    assert len(list((tmp_path / "uploads").iterdir())) == 1


def test_unexpected_error_rolls_back_rows_and_marks_batch_failed(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "ventas.xlsx"
    _create_workbook(
        source,
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86]],
    )

    def fail_calculation(**_: object) -> None:
        raise RuntimeError("falla controlada")

    monkeypatch.setattr("app.services.sales_processor.calculate_units", fail_calculation)

    with pytest.raises(RuntimeError, match="falla controlada"):
        process_sales_file(
            session,
            file_path=source,
            sales_date=date(2026, 8, 8),
            branch="Norte",
            upload_directory=tmp_path / "uploads",
        )

    batch = session.scalar(select(ImportBatch))
    assert batch is not None
    assert batch.status == FAILED
    assert session.scalar(select(func.count(SaleRecord.id))) == 0
    assert session.scalar(select(func.count(Product.id))) == 0
    assert session.scalar(select(func.count(Category.id))) == 0
