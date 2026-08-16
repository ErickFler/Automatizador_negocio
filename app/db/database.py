from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DEFAULT_BRANCHES, settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_directory(database_url: str) -> None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix) and database_url != "sqlite:///:memory:":
        Path(database_url.removeprefix(prefix)).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_directory(settings.database_url)
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_database_tables() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def seed_branches(session: Session) -> None:
    from app.db.models import Branch

    existing_names = set(session.scalars(select(Branch.name)).all())
    missing_branches = [
        Branch(name=name) for name in DEFAULT_BRANCHES if name not in existing_names
    ]
    if missing_branches:
        session.add_all(missing_branches)
        session.commit()


def initialize_database() -> None:
    create_database_tables()
    with SessionLocal() as session:
        seed_branches(session)


def get_db() -> Generator[Session, None, None]:
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()
