from typing import Never

from app.services.raw_import_service import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    FAILED,
    PENDING,
    PROCESSING,
    WAITING_FOR_PRODUCT_MAPPING,
)


def process_sales_file(*_: object, **__: object) -> Never:
    """Bloquea el pipeline monolítico del prototipo anterior.

    Las cargas activas deben pasar por raw_import_service y product_mapping.
    SaleRecord y los cálculos se implementarán en la Etapa 4.
    """
    raise RuntimeError(
        "El procesador anterior fue retirado. Usa el flujo de importación cruda "
        "y homologación confirmado."
    )


__all__ = [
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "FAILED",
    "PENDING",
    "PROCESSING",
    "WAITING_FOR_PRODUCT_MAPPING",
    "process_sales_file",
]
