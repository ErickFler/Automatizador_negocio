from decimal import Decimal

import pytest

from app.domain.calculations import (
    INVALID_CALCULATION_VALUE,
    MISSING_QUANTITY,
    MISSING_SALE_PRICE,
    MISSING_TOTAL_AMOUNT,
    NEGATIVE_COMBO_UNITS,
    NEGATIVE_QUANTITY,
    NEGATIVE_REAL_UNITS,
    NEGATIVE_TOTAL_AMOUNT,
    NON_INTEGER_REAL_UNITS,
    REAL_UNITS_GREATER_THAN_QUANTITY,
    ZERO_SALE_PRICE,
    UnitsCalculationResult,
    calculate_combo_units,
    calculate_real_units,
    calculate_units,
)


def _warning_types(result: UnitsCalculationResult) -> set[str]:
    return {warning.warning_type for warning in result.warnings}


def test_basic_calculation() -> None:
    result = calculate_units(
        quantity=Decimal("2"),
        sale_price=Decimal("43"),
        total_amount=Decimal("86"),
    )

    assert result.real_units == Decimal("2")
    assert result.combo_units == Decimal("0")
    assert result.warnings == ()


def test_combo_calculation() -> None:
    result = calculate_units(
        quantity=Decimal("26"),
        sale_price=Decimal("49"),
        total_amount=Decimal("49"),
    )

    assert result.real_units == Decimal("1")
    assert result.combo_units == Decimal("25")
    assert result.warnings == ()


def test_second_combo_calculation() -> None:
    result = calculate_units(
        quantity=Decimal("16"),
        sale_price=Decimal("49"),
        total_amount=Decimal("147"),
    )

    assert result.real_units == Decimal("3")
    assert result.combo_units == Decimal("13")
    assert result.warnings == ()


def test_zero_sale_price_does_not_crash_or_invent_a_result() -> None:
    result = calculate_units(
        quantity=Decimal("4"),
        sale_price=Decimal("0"),
        total_amount=Decimal("100"),
    )

    assert result.real_units is None
    assert result.combo_units is None
    assert _warning_types(result) == {ZERO_SALE_PRICE}


@pytest.mark.parametrize(
    ("quantity", "sale_price", "total_amount", "warning_type", "real", "combo"),
    [
        (None, Decimal("43"), Decimal("86"), MISSING_QUANTITY, Decimal("2"), None),
        (Decimal("2"), None, Decimal("86"), MISSING_SALE_PRICE, None, None),
        (Decimal("2"), Decimal("43"), None, MISSING_TOTAL_AMOUNT, None, None),
    ],
)
def test_null_values_are_reported(
    quantity: Decimal | None,
    sale_price: Decimal | None,
    total_amount: Decimal | None,
    warning_type: str,
    real: Decimal | None,
    combo: Decimal | None,
) -> None:
    result = calculate_units(
        quantity=quantity,
        sale_price=sale_price,
        total_amount=total_amount,
    )

    assert result.real_units == real
    assert result.combo_units == combo
    assert warning_type in _warning_types(result)


def test_negative_total_amount_is_preserved_and_reported() -> None:
    result = calculate_units(
        quantity=Decimal("2"),
        sale_price=Decimal("43"),
        total_amount=Decimal("-43"),
    )

    assert result.real_units == Decimal("-1")
    assert result.combo_units == Decimal("3")
    assert {NEGATIVE_TOTAL_AMOUNT, NEGATIVE_REAL_UNITS}.issubset(
        _warning_types(result)
    )


def test_negative_quantity_is_preserved_and_reported() -> None:
    result = calculate_units(
        quantity=Decimal("-2"),
        sale_price=Decimal("43"),
        total_amount=Decimal("43"),
    )

    assert result.real_units == Decimal("1")
    assert result.combo_units == Decimal("-3")
    assert {
        NEGATIVE_QUANTITY,
        REAL_UNITS_GREATER_THAN_QUANTITY,
        NEGATIVE_COMBO_UNITS,
    }.issubset(_warning_types(result))


def test_decimal_real_units_are_not_rounded() -> None:
    result = calculate_units(
        quantity=Decimal("10"),
        sale_price=Decimal("4"),
        total_amount=Decimal("9"),
    )

    assert result.real_units == Decimal("2.25")
    assert result.combo_units == Decimal("7.75")
    assert NON_INTEGER_REAL_UNITS in _warning_types(result)


def test_real_units_greater_than_quantity_reports_both_consistency_warnings() -> None:
    result = calculate_units(
        quantity=Decimal("2"),
        sale_price=Decimal("10"),
        total_amount=Decimal("30"),
    )

    assert result.real_units == Decimal("3")
    assert result.combo_units == Decimal("-1")
    assert {
        REAL_UNITS_GREATER_THAN_QUANTITY,
        NEGATIVE_COMBO_UNITS,
    }.issubset(_warning_types(result))


def test_formula_functions_are_independently_available() -> None:
    real_units = calculate_real_units(Decimal("147"), Decimal("49"))

    assert real_units == Decimal("3")
    assert calculate_combo_units(Decimal("16"), real_units) == Decimal("13")


def test_non_finite_value_is_reported_without_crashing() -> None:
    result = calculate_units(
        quantity=Decimal("2"),
        sale_price=Decimal("NaN"),
        total_amount=Decimal("86"),
    )

    assert result.real_units is None
    assert result.combo_units is None
    assert INVALID_CALCULATION_VALUE in _warning_types(result)
