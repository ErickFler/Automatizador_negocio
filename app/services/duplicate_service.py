from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ImportBatch


class DuplicateImportError(ValueError):
    def __init__(self, existing_batch: ImportBatch) -> None:
        self.existing_batch_id = existing_batch.id
        super().__init__(
            "Este archivo ya parece haber sido cargado para esta sucursal y fecha."
        )


def find_duplicate_import(
    session: Session,
    *,
    sha256: str,
    sales_date: date,
    branch_id: int,
) -> ImportBatch | None:
    return session.scalar(
        select(ImportBatch)
        .where(
            ImportBatch.sha256 == sha256,
            ImportBatch.sales_date == sales_date,
            ImportBatch.branch_id == branch_id,
        )
        .order_by(ImportBatch.id)
    )


def ensure_import_is_not_duplicate(
    session: Session,
    *,
    sha256: str,
    sales_date: date,
    branch_id: int,
) -> None:
    existing_batch = find_duplicate_import(
        session,
        sha256=sha256,
        sales_date=sales_date,
        branch_id=branch_id,
    )
    if existing_batch is not None:
        raise DuplicateImportError(existing_batch)
