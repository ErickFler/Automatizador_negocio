from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
import shutil
from uuid import uuid4

from app.core.config import DATA_ROOT

logger = logging.getLogger(__name__)

DEFAULT_UPLOAD_DIRECTORY = DATA_ROOT / "uploads"
_UNSAFE_FILENAME_CHARACTER = re.compile(r"[^\w. -]", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class StoredOriginalFile:
    original_filename: str
    stored_filename: str
    stored_path: Path
    sha256: str


def calculate_sha256(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_original_file(
    source_path: str | Path,
    *,
    original_filename: str | None = None,
    upload_directory: str | Path = DEFAULT_UPLOAD_DIRECTORY,
) -> StoredOriginalFile:
    """Copia el archivo sin modificar sus bytes y devuelve su identidad SHA-256."""
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"No existe el archivo de origen: {source}")

    safe_original_name = _safe_filename(original_filename or source.name)
    destination_directory = Path(upload_directory)
    destination_directory.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid4().hex}_{safe_original_name}"
    stored_path = destination_directory / stored_filename
    shutil.copyfile(source, stored_path)

    source_hash = calculate_sha256(source)
    stored_hash = calculate_sha256(stored_path)
    if stored_hash != source_hash:
        stored_path.unlink(missing_ok=True)
        raise OSError("La copia almacenada no coincide con el archivo original.")

    logger.info("Archivo original almacenado: %s", stored_filename)
    return StoredOriginalFile(
        original_filename=Path(original_filename or source.name).name,
        stored_filename=stored_filename,
        stored_path=stored_path,
        sha256=source_hash,
    )


def _safe_filename(filename: str) -> str:
    basename = Path(filename).name.strip()
    safe_name = _UNSAFE_FILENAME_CHARACTER.sub("_", basename)
    return safe_name or "archivo.xlsx"
