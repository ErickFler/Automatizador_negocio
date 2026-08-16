from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.services.excel_reader import (
    ExcelValidationError,
    REQUIRED_COLUMNS,
    read_sales_excel,
)


def _create_workbook(path: Path, rows: list[list[object]] | None = None) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(REQUIRED_COLUMNS))
    for row in rows or []:
        worksheet.append(row)
    workbook.save(path)


def test_correct_file_is_read_and_converted(tmp_path: Path) -> None:
    file_path = tmp_path / "ventas.xlsx"
    _create_workbook(
        file_path,
        [
            ["BEBIDAS", 139, "REFRESCO PEPSI 2 LTS", 2, 43, 20, 40, 86],
            ["ALIMENTOS", "140", "PAPA GALEANA", 16.5, 49, 15, 240, 147],
        ],
    )

    result = read_sales_excel(file_path)

    assert result.total_rows == 2
    assert result.warning_rows == 0
    assert result.rows[0].source_row_number == 2
    assert result.rows[0].external_product_id == "139"
    assert result.rows[0].quantity == Decimal("2")
    assert result.rows[0].sale_price == Decimal("43")
    assert result.rows[0].unit_cost == Decimal("20")
    assert result.rows[0].cost_amount == Decimal("40")
    assert result.rows[0].total_amount == Decimal("86")
    assert result.rows[1].source_row_number == 3
    assert result.rows[1].quantity == Decimal("16.5")


def test_missing_required_column_stops_reading(tmp_path: Path) -> None:
    file_path = tmp_path / "sin_total.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append([column for column in REQUIRED_COLUMNS if column != "ImporteTotal"])
    worksheet.append(["BEBIDAS", 139, "REFRESCO", 2, 43, 20, 40])
    workbook.save(file_path)

    with pytest.raises(ExcelValidationError) as captured:
        read_sales_excel(file_path)

    assert captured.value.code == "MISSING_COLUMNS"
    assert captured.value.missing_columns == ("ImporteTotal",)
    assert "ImporteTotal" in str(captured.value)


def test_workbook_without_sales_rows_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "vacio.xlsx"
    _create_workbook(file_path)

    with pytest.raises(ExcelValidationError) as captured:
        read_sales_excel(file_path)

    assert captured.value.code == "EMPTY_SPREADSHEET"


def test_zero_byte_file_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "vacio.xlsx"
    file_path.touch()

    with pytest.raises(ExcelValidationError) as captured:
        read_sales_excel(file_path)

    assert captured.value.code == "EMPTY_FILE"


def test_unsupported_file_extension_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "ventas.csv"
    file_path.write_text("NombreProducto\nProducto", encoding="utf-8")

    with pytest.raises(ExcelValidationError) as captured:
        read_sales_excel(file_path)

    assert captured.value.code == "UNSUPPORTED_FILE_FORMAT"


def test_corrupt_workbook_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "corrupto.xlsx"
    file_path.write_bytes(b"esto no es un archivo xlsx")

    with pytest.raises(ExcelValidationError) as captured:
        read_sales_excel(file_path)

    assert captured.value.code == "INVALID_EXCEL_FILE"


def test_corrupt_numeric_value_generates_warning_and_preserves_row(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "valor_corrupto.xlsx"
    _create_workbook(
        file_path,
        [["BEBIDAS", 139, "REFRESCO", "dos", 43, 20, 40, 86]],
    )

    result = read_sales_excel(file_path)

    assert result.total_rows == 1
    assert result.rows[0].quantity is None
    assert result.rows[0].original_values["Cantidad"] == "dos"
    assert result.warnings[0].warning_type == "INVALID_NUMERIC_VALUE"
    assert result.warnings[0].row_number == 2


def test_missing_values_generate_structured_warnings(tmp_path: Path) -> None:
    file_path = tmp_path / "faltantes.xlsx"
    _create_workbook(file_path, [[None, None, None, None, 43, 20, 40, 86]])

    result = read_sales_excel(file_path)

    warning_types = {warning.warning_type for warning in result.warnings}
    assert result.total_rows == 1
    assert result.warning_rows == 1
    assert {
        "MISSING_PRODUCT_ID",
        "MISSING_PRODUCT_NAME",
        "MISSING_CATEGORY",
        "MISSING_NUMERIC_VALUE",
    }.issubset(warning_types)
