from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.database import Base, seed_branches
from app.db.models import Branch, ImportBatch
from app.services.duplicate_service import find_duplicate_import


def test_duplicate_requires_same_hash_date_and_branch_id() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_branches(session)
        branches = session.scalars(select(Branch).order_by(Branch.id)).all()
        first_branch, second_branch = branches[:2]
        batch = ImportBatch(
            sales_date=date(2026, 8, 8),
            branch=first_branch,
            source_filename="ventas.xlsx",
            stored_filename="uuid_ventas.xlsx",
            sha256="a" * 64,
            status="COMPLETED",
        )
        session.add(batch)
        session.commit()

        assert find_duplicate_import(
            session,
            sha256="a" * 64,
            sales_date=date(2026, 8, 8),
            branch_id=first_branch.id,
        ) == batch
        assert find_duplicate_import(
            session,
            sha256="a" * 64,
            sales_date=date(2026, 8, 8),
            branch_id=second_branch.id,
        ) is None
        assert find_duplicate_import(
            session,
            sha256="a" * 64,
            sales_date=date(2026, 8, 9),
            branch_id=first_branch.id,
        ) is None
