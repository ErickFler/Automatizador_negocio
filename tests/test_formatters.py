from decimal import Decimal

from app.presentation.formatters import format_decimal, format_money


def test_units_are_displayed_as_whole_numbers() -> None:
    assert format_decimal(Decimal("1006.000000000000")) == "1,006"
    assert format_decimal(Decimal("12.6")) == "13"
    assert format_decimal("944.000000000000") == "944"


def test_sales_amount_is_displayed_with_two_decimals() -> None:
    assert format_money(Decimal("18290.000000000000")) == "$18,290.00"
