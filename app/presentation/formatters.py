from datetime import date
from decimal import Decimal


DisplayNumber = Decimal | int | float | str | None


def _display_decimal(value: DisplayNumber) -> Decimal:
    return Decimal(0) if value is None else Decimal(str(value))


def format_decimal(value: DisplayNumber) -> str:
    return f"{_display_decimal(value):,.0f}"


def format_money(value: DisplayNumber) -> str:
    return f"${_display_decimal(value):,.2f}"


def format_percent(value: Decimal | None) -> str:
    return "No aplica" if value is None else f"{value:,.2f}%"


def format_date_mx(value: date) -> str:
    return value.strftime("%d/%m/%Y")
