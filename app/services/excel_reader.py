from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "NombreCategoria",
    "IDProducto",
    "NombreProducto",
    "Cantidad",
    "PrecioVenta",
    "Costo",
    "Importe",
    "ImporteTotal",
)
SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm"}
NUMERIC_COLUMNS = {
    "Cantidad": "quantity",
    "PrecioVenta": "sale_price",
    "Costo": "unit_cost",
    "Importe": "cost_amount",
    "ImporteTotal": "total_amount",
}


class ExcelValidationError(ValueError):
    def __init__(
        self, code: str, message: str, *, missing_columns: tuple[str, ...] = ()
    ) -> None:
        super().__init__(message)
        self.code = code
        self.missing_columns = missing_columns


@dataclass(frozen=True, slots=True)
class ExcelWarning:
    row_number: int
    product_id: str | None
    product_name: str | None
    warning_type: str
    description: str
    original_values: dict[str, object | None]


@dataclass(frozen=True, slots=True)
class ExcelSaleRow:
    source_row_number: int
    category_name: str | None
    external_product_id: str | None
    product_name: str | None
    quantity: Decimal | None
    sale_price: Decimal | None
    unit_cost: Decimal | None
    cost_amount: Decimal | None
    total_amount: Decimal | None
    original_values: dict[str, object | None]


@dataclass(slots=True)
class ExcelReadResult:
    rows: list[ExcelSaleRow] = field(default_factory=list)
    warnings: list[ExcelWarning] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.rows)

    @property
    def warning_rows(self) -> int:
        return len({warning.row_number for warning in self.warnings})


def read_sales_excel(file_path: str | Path) -> ExcelReadResult:
    """Lee un reporte y conserva todas sus filas, incluso si tienen advertencias."""
    path = Path(file_path)
    _validate_source_file(path)

    try:
        dataframe = pd.read_excel(path, engine="openpyxl", dtype=object)
    except Exception as exc:
        raise ExcelValidationError(
            "INVALID_EXCEL_FILE",
            "No fue posible abrir el archivo como un libro Excel válido.",
        ) from exc

    _validate_required_columns(dataframe)
    if dataframe.empty:
        raise ExcelValidationError(
            "EMPTY_SPREADSHEET", "El archivo Excel no contiene filas de ventas."
        )

    result = ExcelReadResult()
    for source_row_number, (_, raw_row) in enumerate(
        dataframe.iterrows(), start=2
    ):
        original_values = {
            column: _safe_original_value(raw_row[column])
            for column in REQUIRED_COLUMNS
        }
        product_id = _parse_product_id(raw_row["IDProducto"])
        product_name = _parse_optional_text(raw_row["NombreProducto"])
        category_name = _parse_optional_text(raw_row["NombreCategoria"])

        if product_id is None:
            result.warnings.append(
                _warning(
                    source_row_number,
                    product_id,
                    product_name,
                    "MISSING_PRODUCT_ID",
                    "IDProducto está vacío o es inválido.",
                    original_values,
                )
            )
        if product_name is None or not product_name.strip():
            result.warnings.append(
                _warning(
                    source_row_number,
                    product_id,
                    product_name,
                    "MISSING_PRODUCT_NAME",
                    "NombreProducto está vacío.",
                    original_values,
                )
            )
        if category_name is None or not category_name.strip():
            result.warnings.append(
                _warning(
                    source_row_number,
                    product_id,
                    product_name,
                    "MISSING_CATEGORY",
                    "NombreCategoria está vacío; el producto quedará pendiente de clasificar.",
                    original_values,
                )
            )

        converted_numbers: dict[str, Decimal | None] = {}
        for source_column, target_field in NUMERIC_COLUMNS.items():
            value, error = _parse_decimal(raw_row[source_column])
            converted_numbers[target_field] = value
            if error == "missing":
                result.warnings.append(
                    _warning(
                        source_row_number,
                        product_id,
                        product_name,
                        "MISSING_NUMERIC_VALUE",
                        f"{source_column} está vacío.",
                        original_values,
                    )
                )
            elif error == "invalid":
                result.warnings.append(
                    _warning(
                        source_row_number,
                        product_id,
                        product_name,
                        "INVALID_NUMERIC_VALUE",
                        f"{source_column} no contiene un número válido.",
                        original_values,
                    )
                )

        result.rows.append(
            ExcelSaleRow(
                source_row_number=source_row_number,
                category_name=category_name,
                external_product_id=product_id,
                product_name=product_name,
                quantity=converted_numbers["quantity"],
                sale_price=converted_numbers["sale_price"],
                unit_cost=converted_numbers["unit_cost"],
                cost_amount=converted_numbers["cost_amount"],
                total_amount=converted_numbers["total_amount"],
                original_values=original_values,
            )
        )

    logger.info(
        "Archivo Excel leído: %s | filas=%s | advertencias=%s",
        path.name,
        result.total_rows,
        len(result.warnings),
    )
    return result


def _validate_source_file(path: Path) -> None:
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ExcelValidationError(
            "UNSUPPORTED_FILE_FORMAT",
            "El archivo debe tener extensión .xlsx o .xlsm.",
        )
    if not path.is_file():
        raise ExcelValidationError("FILE_NOT_FOUND", "El archivo no existe.")
    if path.stat().st_size == 0:
        raise ExcelValidationError("EMPTY_FILE", "El archivo está vacío.")


def _validate_required_columns(dataframe: pd.DataFrame) -> None:
    missing_columns = tuple(
        column for column in REQUIRED_COLUMNS if column not in dataframe.columns
    )
    if missing_columns:
        joined_columns = ", ".join(missing_columns)
        raise ExcelValidationError(
            "MISSING_COLUMNS",
            f"Faltan columnas obligatorias: {joined_columns}.",
            missing_columns=missing_columns,
        )


def _parse_optional_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    return str(value)


def _parse_product_id(value: Any) -> str | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _parse_decimal(value: Any) -> tuple[Decimal | None, str | None]:
    if _is_missing(value):
        return None, "missing"
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None, "invalid"
    if not number.is_finite():
        return None, "invalid"
    return number, None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, bool) else False


def _safe_original_value(value: Any) -> object | None:
    if _is_missing(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def _warning(
    row_number: int,
    product_id: str | None,
    product_name: str | None,
    warning_type: str,
    description: str,
    original_values: dict[str, object | None],
) -> ExcelWarning:
    return ExcelWarning(
        row_number=row_number,
        product_id=product_id,
        product_name=product_name,
        warning_type=warning_type,
        description=description,
        original_values=original_values,
    )
