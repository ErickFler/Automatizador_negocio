from decimal import Decimal

import pytest

from app.services.numeric_parser import parse_decimal


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (49, Decimal("49")),
        (49.0, Decimal("49.0")),
        ("49", Decimal("49")),
        ("49.50", Decimal("49.50")),
    ],
)
def test_valid_excel_numeric_forms_are_explicitly_converted(
    raw_value: object, expected: Decimal
) -> None:
    result = parse_decimal(raw_value)

    assert result.is_valid
    assert result.value == expected


@pytest.mark.parametrize("raw_value", [None, "", "ERROR", "NaN", "Infinity"])
def test_invalid_value_is_never_silently_converted_to_zero(raw_value: object) -> None:
    result = parse_decimal(raw_value)

    assert not result.is_valid
    assert result.value is None
    assert result.error
