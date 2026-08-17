from datetime import date
from decimal import Decimal


def format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "0"
    if value == value.to_integral_value():
        return f"{value:,.0f}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def format_money(value: Decimal | None) -> str:
    return f"${(value or Decimal(0)):,.2f}"


def format_percent(value: Decimal | None) -> str:
    return "No aplica" if value is None else f"{value:,.2f}%"


def format_date_mx(value: date) -> str:
    return value.strftime("%d/%m/%Y")
