from dataclasses import dataclass
from decimal import Decimal

MISSING_QUANTITY = "MISSING_QUANTITY"
MISSING_SALE_PRICE = "MISSING_SALE_PRICE"
MISSING_TOTAL_AMOUNT = "MISSING_TOTAL_AMOUNT"
INVALID_CALCULATION_VALUE = "INVALID_CALCULATION_VALUE"
ZERO_SALE_PRICE = "ZERO_SALE_PRICE"
NEGATIVE_QUANTITY = "NEGATIVE_QUANTITY"
NEGATIVE_SALE_PRICE = "NEGATIVE_SALE_PRICE"
NEGATIVE_TOTAL_AMOUNT = "NEGATIVE_TOTAL_AMOUNT"
NEGATIVE_REAL_UNITS = "NEGATIVE_REAL_UNITS"
NON_INTEGER_REAL_UNITS = "NON_INTEGER_REAL_UNITS"
REAL_UNITS_GREATER_THAN_QUANTITY = "REAL_UNITS_GREATER_THAN_QUANTITY"
NEGATIVE_COMBO_UNITS = "NEGATIVE_COMBO_UNITS"


@dataclass(frozen=True, slots=True)
class CalculationWarning:
    warning_type: str
    description: str
    values: dict[str, Decimal | None]


@dataclass(frozen=True, slots=True)
class UnitsCalculationResult:
    real_units: Decimal | None
    combo_units: Decimal | None
    warnings: tuple[CalculationWarning, ...]


def calculate_real_units(
    total_amount: Decimal | None, sale_price: Decimal | None
) -> Decimal | None:
    """Calcula ImporteTotal / PrecioVenta sin redondear ni corregir valores."""
    if (
        total_amount is None
        or sale_price is None
        or not total_amount.is_finite()
        or not sale_price.is_finite()
        or sale_price == 0
    ):
        return None
    return total_amount / sale_price


def calculate_combo_units(
    quantity: Decimal | None, real_units: Decimal | None
) -> Decimal | None:
    """Calcula Cantidad - UnidadesReales sin alterar la cantidad registrada."""
    if (
        quantity is None
        or real_units is None
        or not quantity.is_finite()
        or not real_units.is_finite()
    ):
        return None
    return quantity - real_units


def calculate_units(
    *,
    quantity: Decimal | None,
    sale_price: Decimal | None,
    total_amount: Decimal | None,
) -> UnitsCalculationResult:
    """Aplica las fórmulas aprobadas y detecta casos que requieren revisión."""
    values = {
        "quantity": quantity,
        "sale_price": sale_price,
        "total_amount": total_amount,
    }
    warnings: list[CalculationWarning] = []

    invalid_fields = tuple(
        field_name
        for field_name, value in values.items()
        if value is not None and not value.is_finite()
    )
    for field_name in invalid_fields:
        warnings.append(
            _warning(
                INVALID_CALCULATION_VALUE,
                f"{field_name} no contiene un número finito.",
                values,
            )
        )

    valid_quantity = None if "quantity" in invalid_fields else quantity
    valid_sale_price = None if "sale_price" in invalid_fields else sale_price
    valid_total_amount = None if "total_amount" in invalid_fields else total_amount

    if quantity is None:
        warnings.append(
            _warning(MISSING_QUANTITY, "Cantidad está vacía.", values)
        )
    elif valid_quantity is not None and valid_quantity < 0:
        warnings.append(
            _warning(NEGATIVE_QUANTITY, "Cantidad es negativa.", values)
        )

    if sale_price is None:
        warnings.append(
            _warning(MISSING_SALE_PRICE, "PrecioVenta está vacío.", values)
        )
    elif valid_sale_price is not None and valid_sale_price == 0:
        warnings.append(
            _warning(
                ZERO_SALE_PRICE,
                "PrecioVenta es cero; no es posible dividir ImporteTotal entre cero.",
                values,
            )
        )
    elif valid_sale_price is not None and valid_sale_price < 0:
        warnings.append(
            _warning(NEGATIVE_SALE_PRICE, "PrecioVenta es negativo.", values)
        )

    if total_amount is None:
        warnings.append(
            _warning(MISSING_TOTAL_AMOUNT, "ImporteTotal está vacío.", values)
        )
    elif valid_total_amount is not None and valid_total_amount < 0:
        warnings.append(
            _warning(NEGATIVE_TOTAL_AMOUNT, "ImporteTotal es negativo.", values)
        )

    real_units = calculate_real_units(valid_total_amount, valid_sale_price)
    combo_units = calculate_combo_units(valid_quantity, real_units)

    if real_units is not None:
        if real_units < 0:
            warnings.append(
                _warning(
                    NEGATIVE_REAL_UNITS,
                    "UnidadesReales es negativo.",
                    values,
                )
            )
        if real_units != real_units.to_integral_value():
            warnings.append(
                _warning(
                    NON_INTEGER_REAL_UNITS,
                    "UnidadesReales no es un número entero.",
                    values,
                )
            )
        if valid_quantity is not None and real_units > valid_quantity:
            warnings.append(
                _warning(
                    REAL_UNITS_GREATER_THAN_QUANTITY,
                    "UnidadesReales es mayor que Cantidad.",
                    values,
                )
            )

    if combo_units is not None and combo_units < 0:
        warnings.append(
            _warning(
                NEGATIVE_COMBO_UNITS,
                "UnidadesCombo es negativo.",
                values,
            )
        )

    return UnitsCalculationResult(
        real_units=real_units,
        combo_units=combo_units,
        warnings=tuple(warnings),
    )


def _warning(
    warning_type: str,
    description: str,
    values: dict[str, Decimal | None],
) -> CalculationWarning:
    return CalculationWarning(
        warning_type=warning_type,
        description=description,
        values=values.copy(),
    )
