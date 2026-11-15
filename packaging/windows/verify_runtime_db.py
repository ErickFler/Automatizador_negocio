"""Verificación SQLite usada por el smoke test del artefacto Windows."""

from pathlib import Path
import sqlite3
import sys


def verify(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        branches = connection.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
    return (
        f"integrity={integrity};"
        f"foreign_key_violations={violations};"
        f"branches={branches}"
    )


if __name__ == "__main__":
    print(verify(Path(sys.argv[1])))
