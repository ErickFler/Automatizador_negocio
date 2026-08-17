"""Punto de compatibilidad para el procesador normalizado vigente.

El pipeline monolítico que leía Excel y creaba productos automáticamente ya no
forma parte del código activo. El servicio oficial recibe únicamente lotes
crudos previamente importados y productos confirmados.
"""

from app.services.sales_processing_service import (
    BatchProcessingResult,
    process_normalized_sales_batch,
)

__all__ = ["BatchProcessingResult", "process_normalized_sales_batch"]
