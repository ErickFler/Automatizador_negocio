from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ImportBatch


class DuplicateImportError(ValueError):
    def __init__(self, existing_batch: ImportBatch) -> None:
        self.existing_batch_id = existing_batch.id
        super().__init__(
            f"El mismo archivo ya fue cargado para la fecha y sucursal "
            f"seleccionadas (batch_id={existing_batch.id})."
        )


def find_duplicate_import(
    session: Session,
    *,
    file_sha256: str,
    sales_date: date,
    branch: str,
) -> ImportBatch | None:
    return session.scalar(
        select(ImportBatch)
        .where(
            ImportBatch.file_sha256 == file_sha256,
            ImportBatch.sales_date == sales_date,
            ImportBatch.branch == branch.strip(),
        )
        .order_by(ImportBatch.id)
    )


def ensure_import_is_not_duplicate(
    session: Session,
    *,
    file_sha256: str,
    sales_date: date,
    branch: str,
) -> None:
    existing_batch = find_duplicate_import(
        session,
        file_sha256=file_sha256,
        sales_date=sales_date,
        branch=branch,
    )
    if existing_batch is not None:
        raise DuplicateImportError(existing_batch)

