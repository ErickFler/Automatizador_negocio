import re
import unicodedata

PENDING_CATEGORY_NAME = "Pendiente de clasificar"
_MULTIPLE_WHITESPACE = re.compile(r"\s+")


def clean_display_text(value: str) -> str:
    """Limpia espacios e invisibles sin eliminar acentos ni cambiar capitalización."""
    normalized = unicodedata.normalize("NFC", value)
    without_invisible_characters = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return _MULTIPLE_WHITESPACE.sub(" ", without_invisible_characters).strip()


def normalize_for_comparison(value: str) -> str:
    """Genera una representación estable y no destructiva para comparar textos."""
    return clean_display_text(value).casefold()


def prepare_category_name(value: str | None) -> tuple[str, str]:
    cleaned_name = clean_display_text(value or "")
    display_name = cleaned_name or PENDING_CATEGORY_NAME
    return display_name, normalize_for_comparison(display_name)

