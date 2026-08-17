from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db, seed_branches
from app.db.models import (
    Branch,
    BranchProduct,
    Category,
    CostImportBatch,
    CostImportRow,
    ImportBatch,
    MasterProduct,
    ProductCost,
    RawSaleRecord,
    SaleRecord,
)
from app.domain.normalization import normalize_for_comparison
from app.main import app
from app.services.cost_service import (
    COMPLETED,
    COMPLETED_WITH_WARNINGS,
    FAILED,
    ROW_CONFLICT,
    ROW_IMPORTED,
    WAITING_FOR_PRODUCT_MAPPING,
    CostConflictError,
    DuplicateCostImportError,
    cost_coverage,
    create_product_cost,
    finalize_cost_batch,
    get_cost_import_batch,
    get_effective_cost,
    get_effective_costs,
    import_cost_file,
    list_cost_overview,
    list_pending_cost_rows,
    map_cost_row,
    product_cost_history,
)


@pytest.fixture
def cost_session() -> Generator[tuple[Session, dict[str, object]], None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_branches(session)
    branches = {row.name: row for row in session.scalars(select(Branch))}
    drinks = Category(
        display_name="Bebidas", normalized_name=normalize_for_comparison("Bebidas")
    )
    sides = Category(
        display_name="Complementos",
        normalized_name=normalize_for_comparison("Complementos"),
    )
    coke = _product("Coca Cola 3 L", drinks)
    salad = _product("Ensalada de col", sides)
    puree = _product("Pure Familiar", sides)
    session.add_all([drinks, sides, coke, salad, puree])
    session.flush()
    salad_solidarity = _mapping(session, branches["Solidaridad"], salad, "248")
    salad_alliance = _mapping(session, branches["Alianza"], salad, "254")
    coke_loma = _mapping(session, branches["Loma Linda"], coke, "300")
    _sale(session, 1, date(2026, 8, 10), salad_solidarity, "999", "49")
    _sale(session, 2, date(2026, 8, 11), salad_alliance, "888", "49")
    _sale(session, 3, date(2026, 8, 12), coke_loma, "777", "40")
    session.commit()
    yield session, {
        "branches": branches,
        "drinks": drinks,
        "sides": sides,
        "coke": coke,
        "salad": salad,
        "puree": puree,
        "salad_solidarity": salad_solidarity,
        "salad_alliance": salad_alliance,
    }
    session.close()


@pytest.fixture
def cost_client(cost_session, tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    session, _ = cost_session

    def override_database() -> Generator[Session, None, None]:
        yield session

    import app.api.costs as costs_api

    monkeypatch.setattr(costs_api, "COST_UPLOAD_DIRECTORY", tmp_path / "uploads")
    app.dependency_overrides[get_db] = override_database
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _product(name: str, category: Category) -> MasterProduct:
    return MasterProduct(
        display_name=name,
        normalized_name=normalize_for_comparison(name),
        category=category,
    )


def _mapping(session: Session, branch: Branch, product: MasterProduct, external_id: str) -> BranchProduct:
    row = BranchProduct(
        branch=branch,
        master_product=product,
        external_product_id=external_id,
        original_product_name=product.display_name,
        normalized_product_name=product.normalized_name,
        original_category_name=product.category.display_name,
    )
    session.add(row)
    session.flush()
    return row


def _sale(
    session: Session,
    number: int,
    sales_date: date,
    mapping: BranchProduct,
    original_cost: str,
    sale_price: str,
) -> None:
    batch = ImportBatch(
        sales_date=sales_date,
        branch=mapping.branch,
        source_filename=f"cost_sale_{number}.xlsx",
        stored_filename=f"cost_sale_{number}_stored.xlsx",
        sha256=f"{number:064d}",
        status="COMPLETED",
        total_rows=1,
        normalized_rows=1,
    )
    raw = RawSaleRecord(
        import_batch=batch,
        source_row_number=2,
        external_product_id=mapping.external_product_id,
        original_product_name=mapping.original_product_name,
        quantity_raw="1",
        sale_price_raw=sale_price,
        cost_raw=original_cost,
        total_amount_raw=sale_price,
    )
    session.add(
        SaleRecord(
            batch=batch,
            raw_sale_record=raw,
            branch=mapping.branch,
            branch_product=mapping,
            master_product=mapping.master_product,
            sales_date=sales_date,
            total_units=Decimal("1"),
            sale_price=Decimal(sale_price),
            original_cost=Decimal(original_cost),
            sales_amount=Decimal(sale_price),
            individual_sale_units=Decimal("1"),
            combo_units=Decimal("0"),
            source_row_number=2,
        )
    )


def _excel(path: Path, rows: list[dict[str, object]], columns=None) -> Path:
    pd.DataFrame(rows, columns=columns).to_excel(path, index=False)
    return path


def test_create_product_cost_decimal_relationship_and_uniqueness(cost_session) -> None:
    session, context = cost_session
    salad = context["salad"]
    row = create_product_cost(
        session,
        master_product_id=salad.id,
        effective_from=date(2026, 8, 1),
        cost=Decimal("12.50"),
    )
    assert row.cost == Decimal("12.500000000000")
    assert row.master_product_id == salad.id
    assert row.master_product.display_name == "Ensalada de col"
    with pytest.raises(CostConflictError):
        create_product_cost(
            session,
            master_product_id=salad.id,
            effective_from=date(2026, 8, 1),
            cost=Decimal("13"),
        )
    session.add(ProductCost(master_product_id=salad.id, effective_from=date(2026, 8, 1), cost=Decimal("99")))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_multiple_historical_costs_and_effective_resolution(cost_session) -> None:
    session, context = cost_session
    coke = context["coke"]
    for effective_from, cost in (
        (date(2026, 1, 1), "30"),
        (date(2026, 7, 1), "33"),
        (date(2026, 9, 15), "35"),
    ):
        create_product_cost(
            session,
            master_product_id=coke.id,
            effective_from=effective_from,
            cost=Decimal(cost),
        )
    assert get_effective_cost(session, master_product_id=coke.id, sales_date=date(2026, 1, 1)).cost == Decimal("30")
    middle = get_effective_cost(session, master_product_id=coke.id, sales_date=date(2026, 8, 10))
    assert middle.cost == Decimal("33") and middle.effective_from == date(2026, 7, 1)
    assert get_effective_cost(session, master_product_id=coke.id, sales_date=date(2027, 1, 1)).cost == Decimal("35")
    assert get_effective_cost(session, master_product_id=coke.id, sales_date=date(2025, 12, 31)) is None
    assert [row.cost for row in product_cost_history(session, master_product_id=coke.id)] == [Decimal("35"), Decimal("33"), Decimal("30")]


def test_costs_do_not_mix_products_and_bulk_api_is_historical(cost_session) -> None:
    session, context = cost_session
    coke, salad = context["coke"], context["salad"]
    create_product_cost(session, master_product_id=coke.id, effective_from=date(2026, 8, 1), cost=Decimal("30"))
    create_product_cost(session, master_product_id=salad.id, effective_from=date(2026, 8, 1), cost=Decimal("12.5"))
    resolved = get_effective_costs(
        session,
        [(coke.id, date(2026, 8, 10)), (salad.id, date(2026, 8, 10)), (salad.id, date(2026, 7, 1))],
    )
    assert resolved[(coke.id, date(2026, 8, 10))].cost == Decimal("30")
    assert resolved[(salad.id, date(2026, 8, 10))].cost == Decimal("12.5")
    assert resolved[(salad.id, date(2026, 7, 1))] is None


def test_same_master_cost_applies_to_both_salad_branch_products(cost_session) -> None:
    session, context = cost_session
    salad = context["salad"]
    create_product_cost(session, master_product_id=salad.id, effective_from=date(2026, 8, 1), cost=Decimal("12.50"))
    solidarity = context["salad_solidarity"]
    alliance = context["salad_alliance"]
    assert solidarity.master_product_id == alliance.master_product_id == salad.id
    assert get_effective_cost(session, master_product_id=solidarity.master_product_id, sales_date=date(2026, 8, 10)).cost == Decimal("12.50")
    assert get_effective_cost(session, master_product_id=alliance.master_product_id, sales_date=date(2026, 8, 11)).cost == Decimal("12.50")
    assert not hasattr(ProductCost, "branch_id") and not hasattr(ProductCost, "branch_product_id")


def test_original_cost_and_sale_price_are_never_official_cost(cost_session) -> None:
    session, context = cost_session
    salad = context["salad"]
    create_product_cost(session, master_product_id=salad.id, effective_from=date(2026, 8, 1), cost=Decimal("12.50"))
    sales = list(session.scalars(select(SaleRecord).where(SaleRecord.master_product_id == salad.id)))
    assert {row.original_cost for row in sales} == {Decimal("888"), Decimal("999")}
    assert {row.sale_price for row in sales} == {Decimal("49")}
    assert get_effective_cost(session, master_product_id=salad.id, sales_date=date(2026, 8, 10)).cost == Decimal("12.50")


def test_valid_excel_import_and_second_historical_file(cost_session, tmp_path) -> None:
    session, context = cost_session
    first = _excel(tmp_path / "costos_agosto.xlsx", [
        {"Producto": "Coca Cola 3 L", "Costo": 30},
        {"Producto": "Ensalada de col", "Costo": 12.5},
        {"Producto": "Pure Familiar", "Costo": 8.25},
    ])
    second = _excel(tmp_path / "costos_septiembre.xlsx", [
        {"Producto": "Coca Cola 3 L", "Costo": 33},
        {"Producto": "Ensalada de col", "Costo": 13},
        {"Producto": "Pure Familiar", "Costo": 9},
    ])
    august = import_cost_file(session, file_path=first, original_filename=first.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    september = import_cost_file(session, file_path=second, original_filename=second.name, effective_from=date(2026, 9, 1), upload_directory=tmp_path / "stored")
    assert august.status == september.status == COMPLETED
    assert august.imported_rows == september.imported_rows == 3
    assert get_effective_cost(session, master_product_id=context["coke"].id, sales_date=date(2026, 8, 31)).cost == Decimal("30")
    assert get_effective_cost(session, master_product_id=context["coke"].id, sales_date=date(2026, 9, 1)).cost == Decimal("33")
    batch = session.get(CostImportBatch, august.batch_id)
    assert (tmp_path / "stored" / batch.stored_filename).is_file()
    assert len(batch.sha256) == 64


def test_invalid_excel_structure_is_failed_without_costs(cost_session, tmp_path) -> None:
    session, _ = cost_session
    invalid = _excel(tmp_path / "invalid.xlsx", [{"Artículo": "X", "Precio": 10}])
    result = import_cost_file(session, file_path=invalid, original_filename=invalid.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    assert result.status == FAILED and result.imported_rows == 0
    assert session.scalar(select(ProductCost.id)) is None
    assert "Producto y Costo" in session.get(CostImportBatch, result.batch_id).error_message


def test_non_numeric_negative_empty_and_zero_costs(cost_session, tmp_path) -> None:
    session, context = cost_session
    file = _excel(tmp_path / "warnings.xlsx", [
        {"Producto": "Coca Cola 3 L", "Costo": "abc"},
        {"Producto": "Ensalada de col", "Costo": -2},
        {"Producto": "Pure Familiar", "Costo": 0},
        {"Producto": None, "Costo": 5},
    ])
    result = import_cost_file(session, file_path=file, original_filename=file.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    assert result.status == COMPLETED_WITH_WARNINGS
    assert result.imported_rows == 1 and result.warning_rows == 4
    assert get_effective_cost(session, master_product_id=context["puree"].id, sales_date=date(2026, 8, 1)).cost == Decimal("0")
    assert get_effective_cost(session, master_product_id=context["coke"].id, sales_date=date(2026, 8, 1)) is None
    assert get_effective_cost(session, master_product_id=context["salad"].id, sales_date=date(2026, 8, 1)) is None


def test_unknown_product_waits_then_manual_mapping_is_atomic(cost_session, tmp_path) -> None:
    session, context = cost_session
    file = _excel(tmp_path / "unknown.xlsx", [
        {"Producto": "Ensalada de col", "Costo": 12.5},
        {"Producto": "Puré escrito distinto", "Costo": 8.25},
    ])
    pending = import_cost_file(session, file_path=file, original_filename=file.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    assert pending.status == WAITING_FOR_PRODUCT_MAPPING and pending.pending_rows == 1
    assert session.scalar(select(ProductCost.id)) is None
    row = list_pending_cost_rows(session, batch_id=pending.batch_id)[0]
    completed = map_cost_row(session, row_id=row.id, master_product_id=context["puree"].id)
    assert completed.status == COMPLETED_WITH_WARNINGS
    assert completed.imported_rows == 2
    assert get_effective_cost(session, master_product_id=context["puree"].id, sales_date=date(2026, 8, 1)).cost == Decimal("8.25")


def test_duplicate_file_conflicts_and_finalize_are_idempotent(cost_session, tmp_path) -> None:
    session, _ = cost_session
    file = _excel(tmp_path / "same.xlsx", [{"Producto": "Ensalada de col", "Costo": 12.5}])
    result = import_cost_file(session, file_path=file, original_filename=file.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    with pytest.raises(DuplicateCostImportError):
        import_cost_file(session, file_path=file, original_filename=file.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    repeated = finalize_cost_batch(session, batch_id=result.batch_id)
    assert repeated.imported_rows == 1
    assert session.scalar(select(ProductCost).where(ProductCost.cost_import_batch_id == result.batch_id)).cost == Decimal("12.5")

    other = _excel(tmp_path / "conflict.xlsx", [{"Producto": "Ensalada de col", "Costo": 14}])
    conflict = import_cost_file(session, file_path=other, original_filename=other.name, effective_from=date(2026, 8, 1), upload_directory=tmp_path / "stored")
    conflict_batch = get_cost_import_batch(session, batch_id=conflict.batch_id)
    assert conflict.status == COMPLETED_WITH_WARNINGS and conflict.imported_rows == 0
    assert conflict_batch.rows[0].status == ROW_CONFLICT


def test_overview_missing_filter_and_coverage(cost_session) -> None:
    session, context = cost_session
    create_product_cost(session, master_product_id=context["salad"].id, effective_from=date(2026, 8, 1), cost=Decimal("12.5"))
    rows = list_cost_overview(session, reference_date=date(2026, 8, 10))
    missing = list_cost_overview(session, reference_date=date(2026, 8, 10), missing_only=True)
    assert len(rows) == 3 and {row.product.display_name for row in missing} == {"Coca Cola 3 L", "Pure Familiar"}
    coverage = cost_coverage(session, date_from=date(2026, 8, 10), date_to=date(2026, 8, 12))
    assert coverage.products_with_cost == 1
    assert coverage.products_without_cost == 1
    assert coverage.sale_records_without_cost == 1


def test_cost_pages_index_import_history_and_navigation(cost_client, cost_session) -> None:
    _, context = cost_session
    index = cost_client.get("/costs?effective_on=2026-08-10")
    detail = cost_client.get(f"/costs/products/{context['salad'].id}?effective_on=2026-08-10")
    form = cost_client.get("/costs/import")
    assert index.status_code == detail.status_code == form.status_code == 200
    assert "Costos oficiales" in index.text and "Solo productos sin costo" in index.text
    assert "No disponible" in index.text
    assert "Historial de costos" in detail.text
    assert "Producto" in form.text and "Costo" in form.text
    assert 'href="/costs"' in cost_client.get("/").text


def test_http_import_duplicate_and_result(cost_client, cost_session, tmp_path) -> None:
    file = _excel(tmp_path / "http_costs.xlsx", [{"Producto": "Ensalada de col", "Costo": 12.5}])
    content = file.read_bytes()
    response = cost_client.post(
        "/costs/import",
        data={"effective_from": "2026-08-01"},
        files={"file": (file.name, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        follow_redirects=True,
    )
    duplicate = cost_client.post(
        "/costs/import",
        data={"effective_from": "2026-08-01"},
        files={"file": (file.name, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    assert "Costos guardados" in response.text and "Ensalada de col" in response.text
    assert duplicate.status_code == 409
    assert "mismo archivo de costos" in duplicate.text


def test_http_unknown_product_mapping_has_no_create_action(cost_client, tmp_path) -> None:
    file = _excel(tmp_path / "http_unknown.xlsx", [{"Producto": "Nombre desconocido", "Costo": 7}])
    response = cost_client.post(
        "/costs/import",
        data={"effective_from": "2026-08-01"},
        files={"file": (file.name, file.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        follow_redirects=True,
    )
    assert "Hay productos por confirmar" in response.text
    mapping_url = response.url.path.replace("/imports/", "/imports/") + "/products"
    mapping = cost_client.get(mapping_url)
    assert mapping.status_code == 200
    assert "Buscar producto existente" in mapping.text
    assert "Omitir por ahora" in mapping.text
    assert "Crear producto nuevo" not in mapping.text
