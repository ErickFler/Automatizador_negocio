from decimal import Decimal


def requires_product_mapping(sale_price: Decimal | None) -> bool:
    """Only rows that can produce a normalized sale require a catalog mapping."""
    return sale_price is not None and sale_price != 0
