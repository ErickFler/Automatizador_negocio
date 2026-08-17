from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class DecimalParseResult:
    value: Decimal | None
    error: str | None

    @property
    def is_valid(self) -> bool:
        return self.error is None


def parse_decimal(raw_value: object | None) -> DecimalParseResult:
    """Convierte un valor explícito a Decimal; jamás sustituye un error por cero."""
    if raw_value is None:
        return DecimalParseResult(value=None, error="El valor está vacío.")

    text = str(raw_value).strip()
    if not text:
        return DecimalParseResult(value=None, error="El valor está vacío.")

    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return DecimalParseResult(value=None, error="El valor no es numérico.")

    if not value.is_finite():
        return DecimalParseResult(value=None, error="El valor no es un número finito.")
    return DecimalParseResult(value=value, error=None)
