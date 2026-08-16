from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.database import Base, seed_branches
from app.db.models import (
    Branch,
    BranchProduct,
    ImportBatch,
    MasterProduct,
    ProcessingIssue,
    RawSaleRecord,
)
from app.services.duplicate_service import DuplicateImportError
from app.services.excel_reader import REQUIRED_COLUMNS
from app.services.file_storage import calculate_sha256
from app.services.raw_import_service import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    FAILED,
    import_raw_sales_file,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        seed_branches(database_session)
        yield database_session


def _branch(session: Session, name: str = "Loma Linda") -> Branch:
    branch = session.scalar(select(Branch).where(Branch.name == name))
    assert branch is not None
    return branch


def _workbook(
    path: Path,
    rows: list[list[object]],
    *,
    columns: tuple[str, ...] = REQUIRED_COLUMNS,
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(columns))
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    return path


def _valid_rows() -> list[list[object]]:
    return [
        ["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40, 86],
        ["ALIMENTOS", 140, "PAPA", 3, 49, 15, 45, 147],
    ]


def _import(
    session: Session,
    path: Path,
    upload_directory: Path,
    *,
    branch_name: str = "Loma Linda",
    sales_date: date = date(2026, 8, 8),
):
    return import_raw_sales_file(
        session,
        file_path=path,
        original_filename=path.name,
        sales_date=sales_date,
        branch=_branch(session, branch_name),
        upload_directory=upload_directory,
    )


@pytest.mark.parametrize("extension", [".xlsx", ".xlsm"])
def test_valid_workbook_creates_completed_batch_and_preserves_file(
    session: Session, tmp_path: Path, extension: str
) -> None:
    source = _workbook(tmp_path / f"ventas{extension}", _valid_rows())
    upload_directory = tmp_path / "uploads"

    result = _import(session, source, upload_directory)
    batch = session.get(ImportBatch, result.batch_id)

    assert batch is not None
    assert result.status == COMPLETED
    assert batch.branch.name == "Loma Linda"
    assert batch.sha256 == calculate_sha256(source)
    assert batch.source_filename == source.name
    stored = upload_directory / batch.stored_filename
    assert stored.read_bytes() == source.read_bytes()
    assert stored.name != source.name
    assert batch.total_rows == 2
    assert batch.valid_rows == 2
    assert batch.warning_rows == 0


@pytest.mark.parametrize(
    ("filename", "content", "expected_issue"),
    [
        ("ventas.csv", b"no es excel", "UNSUPPORTED_FILE_FORMAT"),
        ("vacio.xlsx", b"", "EMPTY_FILE"),
        ("corrupto.xlsx", b"no es un libro", "INVALID_EXCEL_FILE"),
    ],
)
def test_invalid_or_empty_file_creates_failed_batch(
    session: Session,
    tmp_path: Path,
    filename: str,
    content: bytes,
    expected_issue: str,
) -> None:
    source = tmp_path / filename
    source.write_bytes(content)

    result = _import(session, source, tmp_path / "uploads")
    issue = session.scalar(
        select(ProcessingIssue).where(
            ProcessingIssue.import_batch_id == result.batch_id
        )
    )

    assert result.status == FAILED
    assert issue is not None
    assert issue.issue_type == expected_issue
    assert issue.severity == "ERROR"
    assert session.scalar(select(func.count(RawSaleRecord.id))) == 0


def test_workbook_without_sales_rows_creates_failed_batch(
    session: Session, tmp_path: Path
) -> None:
    source = _workbook(tmp_path / "sin_filas.xlsx", [])

    result = _import(session, source, tmp_path / "uploads")
    issue = session.scalar(
        select(ProcessingIssue).where(
            ProcessingIssue.import_batch_id == result.batch_id
        )
    )

    assert result.status == FAILED
    assert issue is not None
    assert issue.issue_type == "EMPTY_SPREADSHEET"


def test_missing_required_column_fails_without_partial_rows(
    session: Session, tmp_path: Path
) -> None:
    columns = tuple(column for column in REQUIRED_COLUMNS if column != "ImporteTotal")
    source = _workbook(
        tmp_path / "incompleto.xlsx",
        [["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40]],
        columns=columns,
    )

    result = _import(session, source, tmp_path / "uploads")
    batch = session.get(ImportBatch, result.batch_id)

    assert result.status == FAILED
    assert batch is not None
    assert "ImporteTotal" in (batch.error_message or "")
    assert len(batch.raw_sale_records) == 0


def test_duplicate_is_rejected_but_other_branch_and_date_are_allowed(
    session: Session, tmp_path: Path
) -> None:
    source = _workbook(tmp_path / "ventas.xlsx", _valid_rows())
    uploads = tmp_path / "uploads"
    _import(session, source, uploads)

    with pytest.raises(DuplicateImportError, match="ya parece haber sido cargado"):
        _import(session, source, uploads)

    other_branch = _import(
        session, source, uploads, branch_name="Solidaridad"
    )
    other_date = _import(
        session, source, uploads, sales_date=date(2026, 8, 9)
    )

    assert other_branch.status == COMPLETED
    assert other_date.status == COMPLETED
    assert session.scalar(select(func.count(ImportBatch.id))) == 3


def test_all_raw_rows_and_source_row_numbers_are_preserved(
    session: Session, tmp_path: Path
) -> None:
    source = _workbook(tmp_path / "ventas.xlsx", _valid_rows())

    result = _import(session, source, tmp_path / "uploads")
    records = session.scalars(
        select(RawSaleRecord)
        .where(RawSaleRecord.import_batch_id == result.batch_id)
        .order_by(RawSaleRecord.source_row_number)
    ).all()

    assert len(records) == 2
    assert [record.source_row_number for record in records] == [2, 3]
    assert records[0].original_category_name == "BEBIDAS"
    assert records[0].external_product_id == "139"
    assert records[0].quantity_raw == "2"
    assert records[0].sale_price_raw == "43"
    assert records[0].cost_raw == "20"
    assert records[0].amount_raw == "40"
    assert records[0].total_amount_raw == "86"


def test_invalid_numeric_row_is_preserved_and_creates_issue(
    session: Session, tmp_path: Path
) -> None:
    rows = [
        ["BEBIDAS", 139, "REFRESCO", 2, "ERROR", 20, 40, 86],
        ["ALIMENTOS", 140, "PAPA", 3, 49, 15, 45, 147],
    ]
    source = _workbook(tmp_path / "incidencia.xlsx", rows)

    result = _import(session, source, tmp_path / "uploads")
    invalid_record = session.scalar(
        select(RawSaleRecord).where(RawSaleRecord.source_row_number == 2)
    )
    issue = session.scalar(
        select(ProcessingIssue).where(
            ProcessingIssue.raw_sale_record_id == invalid_record.id
        )
    )

    assert result.status == COMPLETED_WITH_WARNINGS
    assert result.total_rows == 2
    assert result.valid_rows == 1
    assert result.issue_rows == 1
    assert invalid_record.sale_price_raw == "ERROR"
    assert issue is not None
    assert issue.issue_type == "INVALID_NUMERIC_VALUE"
    assert issue.source_row_number == 2
    assert issue.severity == "ERROR"


def test_import_does_not_create_products_or_calculate_units(
    session: Session, tmp_path: Path
) -> None:
    source = _workbook(tmp_path / "ventas.xlsx", _valid_rows())

    _import(session, source, tmp_path / "uploads")

    assert session.scalar(select(func.count(MasterProduct.id))) == 0
    assert session.scalar(select(func.count(BranchProduct.id))) == 0
    raw_columns = set(RawSaleRecord.__table__.columns.keys())
    assert "real_units" not in raw_columns
    assert "combo_units" not in raw_columns
