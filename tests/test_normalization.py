from app.domain.normalization import (
    PENDING_CATEGORY_NAME,
    clean_display_text,
    normalize_for_comparison,
    prepare_category_name,
)


def test_clean_display_text_preserves_accents_and_original_case() -> None:
    value = "  CAFÉ\u200b   Frío  "

    assert clean_display_text(value) == "CAFÉ Frío"


def test_normalize_for_comparison_ignores_case_and_extra_spaces() -> None:
    assert normalize_for_comparison("  Refresco   PEPSI ") == "refresco pepsi"


def test_empty_category_is_marked_as_pending() -> None:
    display_name, normalized_name = prepare_category_name(" \u200b ")

    assert display_name == PENDING_CATEGORY_NAME
    assert normalized_name == "pendiente de clasificar"

