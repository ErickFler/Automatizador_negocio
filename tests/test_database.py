from sqlalchemy import inspect, select

from app.core.config import DEFAULT_BRANCHES
from app.db.database import SessionLocal, engine, initialize_database, seed_branches
from app.db.models import Branch


def test_initial_tables_are_created() -> None:
    initialize_database()

    table_names = set(inspect(engine).get_table_names())
    assert "branches" in table_names


def test_four_expected_branches_are_seeded() -> None:
    initialize_database()

    with SessionLocal() as session:
        branch_names = set(session.scalars(select(Branch.name)).all())

    assert set(DEFAULT_BRANCHES).issubset(branch_names)


def test_branch_seed_is_idempotent() -> None:
    initialize_database()

    with SessionLocal() as session:
        seed_branches(session)
        seed_branches(session)
        expected_rows = session.scalars(
            select(Branch).where(Branch.name.in_(DEFAULT_BRANCHES))
        ).all()

    assert len(expected_rows) == 4
