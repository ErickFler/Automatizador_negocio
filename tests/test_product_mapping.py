from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.database import Base, seed_branches
from app.db.models import (
    Branch,
    BranchProduct,
    ImportBatch,
    MasterProduct,
    ProcessingIssue,
    RawSaleRecord,
)
from app.services.excel_reader import REQUIRED_COLUMNS
from app.services.product_catalog import (
    create_branch_product,
    create_master_product,
    get_or_create_category,
)
from app.services.product_mapping import (
    create_and_map_product,
    list_exact_mapping_candidates,
    list_pending_products,
    map_exact_product_matches,
    map_to_existing_product,
)
from app.services.raw_import_service import (
    COMPLETED_WITH_WARNINGS,
    WAITING_FOR_PRODUCT_MAPPING,
    import_raw_sales_file,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        seed_branches(database_session)
        yield database_session


def _branch(session: Session, name: str = "Loma Linda") -> Branch:
    branch = session.scalar(select(Branch).where(Branch.name == name))
    assert branch is not None
    return branch


def _master(
    session: Session,
    *,
    name: str = "Coca Cola 3 L",
    category_name: str = "Bebidas",
) -> MasterProduct:
    category, _ = get_or_create_category(session, display_name=category_name)
    master = create_master_product(session, display_name=name, category=category)
    session.commit()
    return master


def _batch(
    session: Session,
    *,
    branch_name: str = "Loma Linda",
    products: list[tuple[str, str, str | None]] | None = None,
    number: int = 1,
) -> ImportBatch:
    batch = ImportBatch(
        sales_date=date(2026, 8, number),
        branch=_branch(session, branch_name),
        source_filename=f"ventas_{number}.xlsx",
        stored_filename=f"uuid_ventas_{number}.xlsx",
        sha256=f"{number:064d}",
        status=WAITING_FOR_PRODUCT_MAPPING,
    )
    session.add(batch)
    session.flush()
    for row_number, (external_id, name, category) in enumerate(
        products or [("344", "REFRESCO COCA COLA 3 LTS", "BEBIDAS")], start=2
    ):
        session.add(
            RawSaleRecord(
                import_batch=batch,
                source_row_number=row_number,
                external_product_id=external_id,
                original_product_name=name,
                original_category_name=category,
                quantity_raw="1",
                sale_price_raw="50",
                cost_raw="20",
                amount_raw="50",
                total_amount_raw="50",
            )
        )
    batch.total_rows = len(products or [("", "", None)])
    batch.normalized_rows = 0
    session.commit()
    return batch


def test_known_product_does_not_require_mapping_and_new_product_is_grouped(
    session: Session,
) -> None:
    master = _master(session)
    create_branch_product(
        session,
        branch=_branch(session),
        external_product_id="344",
        original_product_name="REFRESCO COCA COLA 3 LTS",
        original_category_name="BEBIDAS",
        master_product=master,
    )
    batch = _batch(
        session,
        products=[
            ("344", "REFRESCO COCA COLA 3 LTS", "BEBIDAS"),
            ("500", "PRODUCTO NUEVO", "VARIOS"),
            ("500", "PRODUCTO NUEVO", "VARIOS"),
        ],
    )

    pending = list_pending_products(session, batch_id=batch.id)

    assert len(pending) == 1
    assert pending[0].external_product_id == "500"
    assert pending[0].row_count == 2


def test_mapping_to_existing_product_persists_and_future_batch_does_not_ask(
    session: Session,
) -> None:
    master = _master(session)
    batch = _batch(session)

    branch_product = map_to_existing_product(
        session,
        batch_id=batch.id,
        external_product_id="344",
        master_product_id=master.id,
    )
    future_batch = _batch(session, number=2)

    assert branch_product.master_product_id == master.id
    assert branch_product.branch.name == "Loma Linda"
    assert list_pending_products(session, batch_id=batch.id) == []
    assert list_pending_products(session, batch_id=future_batch.id) == []
    assert session.get(ImportBatch, batch.id).status == "COMPLETED"


def test_create_new_product_reuses_normalized_category_and_creates_mapping(
    session: Session,
) -> None:
    existing_category, _ = get_or_create_category(session, display_name="BEBIDAS")
    session.commit()
    batch = _batch(session)

    branch_product = create_and_map_product(
        session,
        batch_id=batch.id,
        external_product_id="344",
        master_display_name="Coca Cola 3 L",
        category_display_name="  bebidas ",
    )

    assert branch_product.master_product.category_id == existing_category.id
    assert branch_product.original_product_name == "REFRESCO COCA COLA 3 LTS"
    assert branch_product.original_category_name == "BEBIDAS"
    assert session.get(ImportBatch, batch.id).status == "COMPLETED"


def test_create_new_product_can_create_new_category(session: Session) -> None:
    batch = _batch(
        session,
        products=[("900", "PASTEL CHOCOLATE", "POSTRES")],
    )

    branch_product = create_and_map_product(
        session,
        batch_id=batch.id,
        external_product_id="900",
        master_display_name="Pastel de Chocolate",
        category_display_name="Postres",
    )

    assert branch_product.master_product.category.display_name == "Postres"


def test_partial_progress_persists_until_all_products_are_resolved(
    session: Session,
) -> None:
    first_master = _master(session, name="Producto Uno")
    second_master = _master(session, name="Producto Dos")
    batch = _batch(
        session,
        products=[("1", "PRODUCTO UNO", "BEBIDAS"), ("2", "PRODUCTO DOS", "BEBIDAS")],
    )

    map_to_existing_product(
        session,
        batch_id=batch.id,
        external_product_id="1",
        master_product_id=first_master.id,
    )
    engine = session.get_bind()

    with Session(engine) as resumed_session:
        assert (
            resumed_session.get(ImportBatch, batch.id).status
            == WAITING_FOR_PRODUCT_MAPPING
        )
        assert [
            item.external_product_id
            for item in list_pending_products(resumed_session, batch_id=batch.id)
        ] == ["2"]

        map_to_existing_product(
            resumed_session,
            batch_id=batch.id,
            external_product_id="2",
            master_product_id=second_master.id,
        )
        assert resumed_session.get(ImportBatch, batch.id).status == "COMPLETED"


def test_exact_matches_are_confirmed_together_and_reprocess_waiting_days(
    session: Session,
) -> None:
    _master(session, name="Producto Uno", category_name="Bebidas")
    first = _batch(
        session,
        products=[("1", "PRODUCTO UNO", "BEBIDAS")],
        number=1,
    )
    second = _batch(
        session,
        products=[("1", "PRODUCTO UNO", "BEBIDAS")],
        number=2,
    )

    exact = list_exact_mapping_candidates(session, batch_id=first.id)
    result = map_exact_product_matches(session, batch_id=first.id)

    assert len(exact) == 1
    assert result.mapped_count == 1
    assert set(result.processed_batch_ids) == {first.id, second.id}
    assert session.get(ImportBatch, first.id).status == "COMPLETED"
    assert session.get(ImportBatch, second.id).status == "COMPLETED"


def test_zero_price_product_does_not_require_catalog_mapping(
    session: Session,
) -> None:
    batch = _batch(
        session,
        products=[("359", "MEDIO POLLO ASADO", "PREPARACION")],
    )
    raw = session.scalar(
        select(RawSaleRecord).where(RawSaleRecord.import_batch_id == batch.id)
    )
    assert raw is not None
    raw.sale_price_raw = "0"
    raw.total_amount_raw = "0"
    session.commit()

    assert list_pending_products(session, batch_id=batch.id) == []


def test_two_branches_can_map_different_local_ids_to_same_master(
    session: Session,
) -> None:
    master = _master(session)
    loma_batch = _batch(session, branch_name="Loma Linda", number=1)
    alianza_batch = _batch(
        session,
        branch_name="Alianza",
        products=[("347", "COCA COLA 3 LT", "BEBIDAS")],
        number=2,
    )

    first = map_to_existing_product(
        session, batch_id=loma_batch.id, external_product_id="344", master_product_id=master.id
    )
    second = map_to_existing_product(
        session, batch_id=alianza_batch.id, external_product_id="347", master_product_id=master.id
    )

    assert first.master_product_id == second.master_product_id == master.id
    assert first.external_product_id == "344"
    assert second.external_product_id == "347"


def test_same_external_id_in_two_branches_can_map_to_different_masters(
    session: Session,
) -> None:
    first_master = _master(session, name="Producto Loma")
    second_master = _master(session, name="Producto Unidad")
    first_batch = _batch(
        session,
        branch_name="Loma Linda",
        products=[("100", "PRODUCTO LOMA", "VARIOS")],
        number=1,
    )
    second_batch = _batch(
        session,
        branch_name="Unidad",
        products=[("100", "PRODUCTO UNIDAD", "VARIOS")],
        number=2,
    )

    first = map_to_existing_product(
        session, batch_id=first_batch.id, external_product_id="100", master_product_id=first_master.id
    )
    second = map_to_existing_product(
        session, batch_id=second_batch.id, external_product_id="100", master_product_id=second_master.id
    )

    assert first.master_product_id != second.master_product_id


def test_category_mismatch_records_warning_without_changing_master_category(
    session: Session,
) -> None:
    master = _master(session, category_name="Bebidas")
    batch = _batch(
        session,
        products=[("344", "COCA COLA 3 LT", "REFRESCOS")],
    )

    map_to_existing_product(
        session, batch_id=batch.id, external_product_id="344", master_product_id=master.id
    )
    issue = session.scalar(
        select(ProcessingIssue).where(ProcessingIssue.issue_type == "CATEGORY_MISMATCH")
    )

    assert issue is not None
    assert master.category.display_name == "Bebidas"
    assert session.get(ImportBatch, batch.id).status == COMPLETED_WITH_WARNINGS


def test_known_local_product_name_and_category_changes_are_audited(
    session: Session, tmp_path: Path
) -> None:
    master = _master(session)
    branch_product = create_branch_product(
        session,
        branch=_branch(session),
        external_product_id="344",
        original_product_name="REFRESCO COCA COLA 3 LTS",
        original_category_name="BEBIDAS",
        master_product=master,
    )
    session.commit()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(list(REQUIRED_COLUMNS))
    worksheet.append(["REFRESCOS", 344, "COCA COLA TRES LITROS", 1, 50, 20, 50, 50])
    source = tmp_path / "cambio.xlsx"
    workbook.save(source)

    result = import_raw_sales_file(
        session,
        file_path=source,
        original_filename=source.name,
        sales_date=date(2026, 8, 10),
        branch=_branch(session),
        upload_directory=tmp_path / "uploads",
    )
    issue_types = set(
        session.scalars(
            select(ProcessingIssue.issue_type).where(
                ProcessingIssue.import_batch_id == result.batch_id
            )
        ).all()
    )

    assert result.status == COMPLETED_WITH_WARNINGS
    assert issue_types == {
        "BRANCH_PRODUCT_NAME_CHANGED",
        "BRANCH_PRODUCT_CATEGORY_CHANGED",
    }
    assert branch_product.original_product_name == "REFRESCO COCA COLA 3 LTS"
    assert branch_product.original_category_name == "BEBIDAS"
