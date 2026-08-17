from decimal import Decimal

import pytest

from app.domain.calculations import (
    INVALID_CALCULATION_VALUE,
    MISSING_TOTAL_UNITS,
    MISSING_SALE_PRICE,
    MISSING_SALES_AMOUNT,
    NEGATIVE_COMBO_UNITS,
    NEGATIVE_TOTAL_UNITS,
    NEGATIVE_INDIVIDUAL_SALE_UNITS,
    NEGATIVE_SALES_AMOUNT,
    NON_INTEGER_INDIVIDUAL_SALE_UNITS,
    INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS,
    ZERO_SALE_PRICE,
    UnitsCalculationResult,
    calculate_combo_units,
    calculate_individual_sale_units,
    calculate_units,
)


def _warning_types(result: UnitsCalculationResult) -> set[str]:
    return {warning.warning_type for warning in result.warnings}


def test_basic_calculation() -> None:
    result = calculate_units(
        total_units=Decimal("2"),
        sale_price=Decimal("43"),
        sales_amount=Decimal("86"),
    )

    assert result.individual_sale_units == Decimal("2")
    assert result.combo_units == Decimal("0")
    assert result.warnings == ()


def test_combo_calculation() -> None:
    result = calculate_units(
        total_units=Decimal("26"),
        sale_price=Decimal("49"),
        sales_amount=Decimal("49"),
    )

    assert result.individual_sale_units == Decimal("1")
    assert result.combo_units == Decimal("25")
    assert result.warnings == ()


def test_second_combo_calculation() -> None:
    result = calculate_units(
        total_units=Decimal("16"),
        sale_price=Decimal("49"),
        sales_amount=Decimal("147"),
    )

    assert result.individual_sale_units == Decimal("3")
    assert result.combo_units == Decimal("13")
    assert result.warnings == ()


def test_official_fifteen_piece_example() -> None:
    result = calculate_units(
        total_units=Decimal("15"),
        sale_price=Decimal("10"),
        sales_amount=Decimal("100"),
    )

    assert result.individual_sale_units == Decimal("10")
    assert result.combo_units == Decimal("5")
    assert result.total_units == Decimal("15")


def test_zero_sale_price_does_not_crash_or_invent_a_result() -> None:
    result = calculate_units(
        total_units=Decimal("4"),
        sale_price=Decimal("0"),
        sales_amount=Decimal("100"),
    )

    assert result.individual_sale_units is None
    assert result.combo_units is None
    assert _warning_types(result) == {ZERO_SALE_PRICE}


@pytest.mark.parametrize(
    ("total_units", "sale_price", "sales_amount", "warning_type", "individual", "combo"),
    [
        (None, Decimal("43"), Decimal("86"), MISSING_TOTAL_UNITS, Decimal("2"), None),
        (Decimal("2"), None, Decimal("86"), MISSING_SALE_PRICE, None, None),
        (Decimal("2"), Decimal("43"), None, MISSING_SALES_AMOUNT, None, None),
    ],
)
def test_null_values_are_reported(
    total_units: Decimal | None,
    sale_price: Decimal | None,
    sales_amount: Decimal | None,
    warning_type: str,
    individual: Decimal | None,
    combo: Decimal | None,
) -> None:
    result = calculate_units(
        total_units=total_units,
        sale_price=sale_price,
        sales_amount=sales_amount,
    )

    assert result.individual_sale_units == individual
    assert result.combo_units == combo
    assert warning_type in _warning_types(result)


def test_negative_sales_amount_is_preserved_and_reported() -> None:
    result = calculate_units(
        total_units=Decimal("2"),
        sale_price=Decimal("43"),
        sales_amount=Decimal("-43"),
    )

    assert result.individual_sale_units == Decimal("-1")
    assert result.combo_units == Decimal("3")
    assert {NEGATIVE_SALES_AMOUNT, NEGATIVE_INDIVIDUAL_SALE_UNITS}.issubset(
        _warning_types(result)
    )


def test_negative_total_units_is_preserved_and_reported() -> None:
    result = calculate_units(
        total_units=Decimal("-2"),
        sale_price=Decimal("43"),
        sales_amount=Decimal("43"),
    )

    assert result.individual_sale_units == Decimal("1")
    assert result.combo_units == Decimal("-3")
    assert {
        NEGATIVE_TOTAL_UNITS,
        INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS,
        NEGATIVE_COMBO_UNITS,
    }.issubset(_warning_types(result))


def test_decimal_individual_sale_units_are_not_rounded() -> None:
    result = calculate_units(
        total_units=Decimal("10"),
        sale_price=Decimal("4"),
        sales_amount=Decimal("9"),
    )

    assert result.individual_sale_units == Decimal("2.25")
    assert result.combo_units == Decimal("7.75")
    assert NON_INTEGER_INDIVIDUAL_SALE_UNITS in _warning_types(result)


def test_individual_units_greater_than_total_reports_consistency_warnings() -> None:
    result = calculate_units(
        total_units=Decimal("2"),
        sale_price=Decimal("10"),
        sales_amount=Decimal("30"),
    )

    assert result.individual_sale_units == Decimal("3")
    assert result.combo_units == Decimal("-1")
    assert {
        INDIVIDUAL_SALE_UNITS_EXCEED_TOTAL_UNITS,
        NEGATIVE_COMBO_UNITS,
    }.issubset(_warning_types(result))


def test_formula_functions_are_independently_available() -> None:
    individual_units = calculate_individual_sale_units(
        Decimal("147"), Decimal("49")
    )

    assert individual_units == Decimal("3")
    assert calculate_combo_units(Decimal("16"), individual_units) == Decimal("13")


def test_non_finite_value_is_reported_without_crashing() -> None:
    result = calculate_units(
        total_units=Decimal("2"),
        sale_price=Decimal("NaN"),
        sales_amount=Decimal("86"),
    )

    assert result.individual_sale_units is None
    assert result.combo_units is None
    assert INVALID_CALCULATION_VALUE in _warning_types(result)
