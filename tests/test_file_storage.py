from pathlib import Path

from app.services.file_storage import calculate_sha256, store_original_file


def test_sha256_is_stable_for_duplicate_content(tmp_path: Path) -> None:
    first = tmp_path / "primero.xlsx"
    second = tmp_path / "segundo.xlsx"
    content = b"mismo contenido binario"
    first.write_bytes(content)
    second.write_bytes(content)

    assert calculate_sha256(first) == calculate_sha256(second)
    assert calculate_sha256(first) == (
        "d05126c009ce0baf5454df97b448f112477a89e34893377d6145daa9ebdb3bca"
    )


def test_original_file_is_copied_without_modification(tmp_path: Path) -> None:
    source = tmp_path / "ventas.xlsx"
    upload_directory = tmp_path / "uploads"
    original_bytes = b"bytes exactos del archivo original\x00\xff"
    source.write_bytes(original_bytes)

    stored = store_original_file(
        source,
        original_filename="../ventas sucursal.xlsx",
        upload_directory=upload_directory,
    )

    assert source.read_bytes() == original_bytes
    assert stored.stored_path.read_bytes() == original_bytes
    assert stored.original_filename == "ventas sucursal.xlsx"
    assert stored.stored_path.parent == upload_directory
    assert stored.sha256 == calculate_sha256(source)


def test_stored_names_are_unique(tmp_path: Path) -> None:
    source = tmp_path / "ventas.xlsx"
    source.write_bytes(b"archivo")

    first = store_original_file(source, upload_directory=tmp_path / "uploads")
    second = store_original_file(source, upload_directory=tmp_path / "uploads")

    assert first.stored_filename != second.stored_filename
    assert first.sha256 == second.sha256
