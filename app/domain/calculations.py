from dataclasses import dataclass
from decimal import Decimal, localcontext

MISSING_TOTAL_UNITS = "MISSING_TOTAL_UNITS"
MISSING_SALE_PRICE = "MISSING_SALE_PRICE"
MISSING_SALES_AMOUNT = "MISSING_SALES_AMOUNT"
INVALID_CALCULATION_VALUE = "INVALID_CALCULATION_VALUE"
ZERO_SALE_PRICE = "ZERO_SALE_PRICE"
NEGATIVE_TOTAL_UNITS = "NEGATIVE_TOTAL_UNITS"
NEGATIVE_SALE_PRICE = "NEGATIVE_SALE_PRICE"
NEGATIVE_SALES_AMOUNT = "NEGATIVE_SALES_AMOUNT"
NEGATIVE_INDIVIDUAL_SALE_UNITS = "NEGATIVE_INDIVIDUAL_SALE_UNITS"
NON_INTEGER_INDIVIDUAL_SALE_UNITS = "NON_INTEGER_INDIVIDUAL_SALE_UNITS"
INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS = (
    "INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS"
)
NEGATIVE_COMBO_UNITS = "NEGATIVE_COMBO_UNITS"


@dataclass(frozen=True, slots=True)
class CalculationWarning:
    warning_type: str
    description: str
    values: dict[str, Decimal | None]


@dataclass(frozen=True, slots=True)
class UnitsCalculationResult:
    total_units: Decimal | None
    individual_sale_units: Decimal | None
    combo_units: Decimal | None
    warnings: tuple[CalculationWarning, ...]


def calculate_individual_sale_units(
    sales_amount: Decimal | None, sale_price: Decimal | None
) -> Decimal | None:
    """Calcula ImporteTotal / PrecioVenta sin redondear ni corregir valores."""
    if (
        sales_amount is None
        or sale_price is None
        or not sales_amount.is_finite()
        or not sale_price.is_finite()
        or sale_price == 0
    ):
        return None
    with localcontext() as context:
        context.prec = 28
        return sales_amount / sale_price


def calculate_combo_units(
    total_units: Decimal | None, individual_sale_units: Decimal | None
) -> Decimal | None:
    """Calcula Cantidad - UnidadesCobradas sin alterar la cantidad registrada."""
    if (
        total_units is None
        or individual_sale_units is None
        or not total_units.is_finite()
        or not individual_sale_units.is_finite()
    ):
        return None
    return total_units - individual_sale_units


def calculate_units(
    *,
    total_units: Decimal | None,
    sale_price: Decimal | None,
    sales_amount: Decimal | None,
) -> UnitsCalculationResult:
    """Aplica las fórmulas aprobadas y detecta casos que requieren revisión."""
    values = {
        "total_units": total_units,
        "sale_price": sale_price,
        "sales_amount": sales_amount,
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

    valid_total_units = None if "total_units" in invalid_fields else total_units
    valid_sale_price = None if "sale_price" in invalid_fields else sale_price
    valid_sales_amount = None if "sales_amount" in invalid_fields else sales_amount

    if total_units is None:
        warnings.append(
            _warning(MISSING_TOTAL_UNITS, "Unidades totales está vacío.", values)
        )
    elif valid_total_units is not None and valid_total_units < 0:
        warnings.append(
            _warning(NEGATIVE_TOTAL_UNITS, "Unidades totales es negativo.", values)
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

    if sales_amount is None:
        warnings.append(
            _warning(MISSING_SALES_AMOUNT, "Venta $ está vacía.", values)
        )
    elif valid_sales_amount is not None and valid_sales_amount < 0:
        warnings.append(
            _warning(NEGATIVE_SALES_AMOUNT, "Venta $ es negativa.", values)
        )

    individual_sale_units = calculate_individual_sale_units(
        valid_sales_amount, valid_sale_price
    )
    combo_units = calculate_combo_units(valid_total_units, individual_sale_units)

    if individual_sale_units is not None:
        if individual_sale_units < 0:
            warnings.append(
                _warning(
                    NEGATIVE_INDIVIDUAL_SALE_UNITS,
                    "Las unidades de venta individual son negativas.",
                    values,
                )
            )
        if individual_sale_units != individual_sale_units.to_integral_value():
            warnings.append(
                _warning(
                    NON_INTEGER_INDIVIDUAL_SALE_UNITS,
                    "Las unidades de venta individual no son un número entero.",
                    values,
                )
            )
        if (
            valid_total_units is not None
            and individual_sale_units > valid_total_units
        ):
            warnings.append(
                _warning(
                    INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS,
                    "Las unidades de venta individual son mayores que las unidades totales.",
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
        total_units=valid_total_units,
        individual_sale_units=individual_sale_units,
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
