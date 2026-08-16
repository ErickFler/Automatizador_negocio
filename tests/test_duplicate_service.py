from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import ImportBatch
from app.services.duplicate_service import find_duplicate_import


def test_duplicate_requires_same_hash_date_and_branch() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = ImportBatch(
            sales_date=date(2026, 8, 8),
            branch="Norte",
            source_filename="ventas.xlsx",
            stored_filename="uuid_ventas.xlsx",
            file_sha256="a" * 64,
            status="COMPLETED",
        )
        session.add(batch)
        session.commit()

        assert (
            find_duplicate_import(
                session,
                file_sha256="a" * 64,
                sales_date=date(2026, 8, 8),
                branch="Norte",
            )
            is not None
        )
        assert (
            find_duplicate_import(
                session,
                file_sha256="a" * 64,
                sales_date=date(2026, 8, 9),
                branch="Norte",
            )
            is None
        )
        assert (
            find_duplicate_import(
                session,
                file_sha256="a" * 64,
                sales_date=date(2026, 8, 8),
                branch="Sur",
            )
            is None
        )

