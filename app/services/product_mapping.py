from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    BranchProduct,
    ImportBatch,
    MasterProduct,
    ProcessingIssue,
    RawSaleRecord,
)
from app.domain.import_policy import requires_product_mapping
from app.domain.normalization import normalize_for_comparison
from app.services.product_catalog import (
    create_branch_product,
    create_master_product,
    get_or_create_category,
)
from app.services.raw_import_service import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    WAITING_FOR_PRODUCT_MAPPING,
    WARNING,
)
from app.services.numeric_parser import parse_decimal


@dataclass(frozen=True, slots=True)
class PendingLocalProduct:
    external_product_id: str
    original_product_name: str
    original_category_name: str | None
    row_count: int


@dataclass(frozen=True, slots=True)
class ExactMappingCandidate:
    pending_product: PendingLocalProduct
    master_product: MasterProduct


@dataclass(frozen=True, slots=True)
class BulkMappingResult:
    mapped_count: int
    processed_batch_ids: tuple[int, ...]


def list_pending_products(
    session: Session, *, batch_id: int
) -> list[PendingLocalProduct]:
    batch = session.scalar(
        select(ImportBatch)
        .options(joinedload(ImportBatch.branch))
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        raise ValueError("La carga solicitada no existe.")

    known_external_ids = set(
        session.scalars(
            select(BranchProduct.external_product_id).where(
                BranchProduct.branch_id == batch.branch_id
            )
        ).all()
    )
    rows = session.scalars(
        select(RawSaleRecord)
        .where(RawSaleRecord.import_batch_id == batch_id)
        .order_by(RawSaleRecord.source_row_number)
    ).all()
    grouped: dict[str, PendingLocalProduct] = {}
    for row in rows:
        external_id = (row.external_product_id or "").strip()
        sale_price = parse_decimal(row.sale_price_raw)
        if (
            not external_id
            or external_id in known_external_ids
            or not requires_product_mapping(
                sale_price.value if sale_price.is_valid else None
            )
        ):
            continue
        existing = grouped.get(external_id)
        if existing is None:
            grouped[external_id] = PendingLocalProduct(
                external_product_id=external_id,
                original_product_name=row.original_product_name or "Producto sin nombre",
                original_category_name=row.original_category_name,
                row_count=1,
            )
        else:
            grouped[external_id] = PendingLocalProduct(
                external_product_id=existing.external_product_id,
                original_product_name=existing.original_product_name,
                original_category_name=existing.original_category_name,
                row_count=existing.row_count + 1,
            )
    return list(grouped.values())


def list_exact_mapping_candidates(
    session: Session, *, batch_id: int
) -> list[ExactMappingCandidate]:
    """Return unambiguous same-name, same-category catalog matches."""
    products = session.scalars(
        select(MasterProduct)
        .options(joinedload(MasterProduct.category))
        .where(MasterProduct.active.is_(True))
    ).all()
    products_by_key = {
        (product.normalized_name, product.category.normalized_name): product
        for product in products
    }
    exact: list[ExactMappingCandidate] = []
    for pending in list_pending_products(session, batch_id=batch_id):
        key = (
            normalize_for_comparison(pending.original_product_name),
            normalize_for_comparison(pending.original_category_name or ""),
        )
        product = products_by_key.get(key)
        if product is not None:
            exact.append(
                ExactMappingCandidate(
                    pending_product=pending,
                    master_product=product,
                )
            )
    return exact


def map_exact_product_matches(
    session: Session, *, batch_id: int
) -> BulkMappingResult:
    batch = session.scalar(
        select(ImportBatch)
        .options(joinedload(ImportBatch.branch))
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        raise ValueError("La carga solicitada no existe.")
    exact = list_exact_mapping_candidates(session, batch_id=batch_id)
    try:
        for candidate in exact:
            pending = candidate.pending_product
            create_branch_product(
                session,
                branch=batch.branch,
                external_product_id=pending.external_product_id,
                original_product_name=pending.original_product_name,
                original_category_name=pending.original_category_name,
                master_product=candidate.master_product,
            )
        session.commit()
        processed = process_ready_batches_for_branch(
            session, branch_id=batch.branch_id
        )
        return BulkMappingResult(
            mapped_count=len(exact),
            processed_batch_ids=tuple(processed),
        )
    except Exception:
        session.rollback()
        raise


def map_to_existing_product(
    session: Session,
    *,
    batch_id: int,
    external_product_id: str,
    master_product_id: int,
) -> BranchProduct:
    batch, representative = _mapping_context(
        session, batch_id=batch_id, external_product_id=external_product_id
    )
    master_product = session.get(MasterProduct, master_product_id)
    if master_product is None or not master_product.active:
        raise ValueError("Selecciona un producto existente válido.")

    try:
        branch_product = create_branch_product(
            session,
            branch=batch.branch,
            external_product_id=external_product_id,
            original_product_name=representative.original_product_name or "",
            original_category_name=representative.original_category_name,
            master_product=master_product,
        )
        _record_category_mismatch(
            session,
            batch=batch,
            raw_record=representative,
            master_product=master_product,
        )
        session.commit()
        process_ready_batches_for_branch(session, branch_id=batch.branch_id)
        return branch_product
    except Exception:
        session.rollback()
        raise


def create_and_map_product(
    session: Session,
    *,
    batch_id: int,
    external_product_id: str,
    master_display_name: str,
    category_display_name: str,
) -> BranchProduct:
    batch, representative = _mapping_context(
        session, batch_id=batch_id, external_product_id=external_product_id
    )
    try:
        category, _ = get_or_create_category(
            session, display_name=category_display_name
        )
        master_product = create_master_product(
            session,
            display_name=master_display_name,
            category=category,
        )
        branch_product = create_branch_product(
            session,
            branch=batch.branch,
            external_product_id=external_product_id,
            original_product_name=representative.original_product_name or "",
            original_category_name=representative.original_category_name,
            master_product=master_product,
        )
        session.commit()
        process_ready_batches_for_branch(session, branch_id=batch.branch_id)
        return branch_product
    except Exception:
        session.rollback()
        raise


def refresh_batch_mapping_status(
    session: Session, *, batch_id: int, commit: bool = True
) -> str:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise ValueError("La carga solicitada no existe.")
    if list_pending_products(session, batch_id=batch_id):
        batch.status = WAITING_FOR_PRODUCT_MAPPING
    else:
        has_issues = session.scalar(
            select(ProcessingIssue.id)
            .where(ProcessingIssue.import_batch_id == batch_id)
            .limit(1)
        ) is not None
        batch.status = COMPLETED_WITH_WARNINGS if has_issues else COMPLETED
    if commit:
        session.commit()
    return batch.status


def _mapping_context(
    session: Session, *, batch_id: int, external_product_id: str
) -> tuple[ImportBatch, RawSaleRecord]:
    external_id = external_product_id.strip()
    batch = session.scalar(
        select(ImportBatch)
        .options(joinedload(ImportBatch.branch))
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        raise ValueError("La carga solicitada no existe.")
    if session.scalar(
        select(BranchProduct.id).where(
            BranchProduct.branch_id == batch.branch_id,
            BranchProduct.external_product_id == external_id,
        )
    ) is not None:
        raise ValueError("Este producto ya fue confirmado.")
    rows = session.scalars(
        select(RawSaleRecord)
        .where(RawSaleRecord.import_batch_id == batch_id)
        .order_by(RawSaleRecord.source_row_number)
    ).all()
    representative = next(
        (
            row
            for row in rows
            if (row.external_product_id or "").strip() == external_id
        ),
        None,
    )
    if representative is None:
        raise ValueError("El producto pendiente no pertenece a esta carga.")
    return batch, representative


def _record_category_mismatch(
    session: Session,
    *,
    batch: ImportBatch,
    raw_record: RawSaleRecord,
    master_product: MasterProduct,
) -> None:
    local_category = normalize_for_comparison(raw_record.original_category_name or "")
    if not local_category or local_category == master_product.category.normalized_name:
        return
    session.add(
        ProcessingIssue(
            import_batch=batch,
            raw_sale_record=raw_record,
            source_row_number=raw_record.source_row_number,
            issue_type="CATEGORY_MISMATCH",
            severity=WARNING,
            message=(
                "La categoría recibida es diferente de la categoría del producto "
                "seleccionado. Se conservaron ambas sin hacer cambios automáticos."
            ),
        )
    )


def process_ready_batches_for_branch(
    session: Session, *, branch_id: int
) -> list[int]:
    """Reprocess every waiting batch made ready by a branch-level mapping."""
    from app.services.sales_processing_service import process_normalized_sales_batch

    waiting_ids = session.scalars(
        select(ImportBatch.id)
        .where(
            ImportBatch.branch_id == branch_id,
            ImportBatch.status == WAITING_FOR_PRODUCT_MAPPING,
        )
        .order_by(ImportBatch.sales_date, ImportBatch.id)
    ).all()
    processed_ids: list[int] = []
    for waiting_id in waiting_ids:
        if list_pending_products(session, batch_id=waiting_id):
            continue
        process_normalized_sales_batch(session, batch_id=waiting_id)
        processed_ids.append(waiting_id)
    return processed_ids
