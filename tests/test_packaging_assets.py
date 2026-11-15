from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tomllib

from app import __version__
from app.core.config import PROJECT_ROOT


def test_windows_icon_contains_multiple_resolutions() -> None:
    icon_path = PROJECT_ROOT / "assets" / "app.ico"
    reserved, icon_type, image_count = struct.unpack("<HHH", icon_path.read_bytes()[:6])

    assert reserved == 0
    assert icon_type == 1
    assert image_count >= 7


def test_windows_packaging_files_are_present() -> None:
    required_files = (
        "ConcentradoVentas.spec",
        "generate_version_info.py",
        "verify_runtime_db.py",
        "build.ps1",
        "smoke-test.ps1",
        "installer.iss",
        "build-installer.ps1",
        "README.md",
        "TEST_CHECKLIST.md",
    )
    packaging_directory = PROJECT_ROOT / "packaging" / "windows"

    assert all((packaging_directory / filename).is_file() for filename in required_files)


def test_pyinstaller_spec_includes_web_resources_and_icon() -> None:
    spec_content = (
        PROJECT_ROOT / "packaging" / "windows" / "ConcentradoVentas.spec"
    ).read_text(encoding="utf-8")

    assert "app/templates" in spec_content
    assert "app/static" in spec_content
    assert "assets\" / \"app.ico" in spec_content
    assert "console=False" in spec_content
    assert "upx=False" in spec_content
    assert 'name="ConcentradoVentas"' in spec_content


def test_version_has_one_application_source_and_generates_windows_metadata(
    tmp_path: Path,
) -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    destination = tmp_path / "version_info.txt"

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "packaging" / "windows" / "generate_version_info.py"),
            __version__,
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert result.returncode == 0
    assert f"ProductVersion', '{__version__}'" in destination.read_text(encoding="utf-8")
    assert "CompanyName" not in destination.read_text(encoding="utf-8")


def test_windows_build_is_64_bit_onedir_reproducible_and_smoke_tested() -> None:
    build = (PROJECT_ROOT / "packaging" / "windows" / "build.ps1").read_text(
        encoding="utf-8"
    )
    smoke = (PROJECT_ROOT / "packaging" / "windows" / "smoke-test.ps1").read_text(
        encoding="utf-8"
    )

    assert "Is64BitOperatingSystem" in build
    assert "struct.calcsize('P') * 8" in build
    assert "Remove-Item -Recurse -Force $BuildOutput" in build
    assert "Remove-Item -Recurse -Force $Distribution" in build
    assert "smoke-test.ps1" in build
    for expected in (
        "health",
        "static/css/app.css",
        "system/shutdown",
        "--no-browser",
        "verify_runtime_db.py",
        "RestartProcess",
    ):
        assert expected in smoke


def test_installer_preserves_user_data_and_has_shortcuts() -> None:
    installer = (
        PROJECT_ROOT / "packaging" / "windows" / "installer.iss"
    ).read_text(encoding="utf-8")

    assert "{autoprograms}" in installer
    assert "{autodesktop}" in installer
    assert "PrivilegesRequired=lowest" in installer
    assert "MyAppPublisher" not in installer
    assert "LOCALAPPDATA\\ConcentradoVentas" not in installer
    assert "delete" not in installer.lower()


def test_packaged_runtime_database_verifier(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT)")
        connection.executemany(
            "INSERT INTO branches (name) VALUES (?)",
            [("Loma Linda",), ("Solidaridad",), ("Alianza",), ("Unidad",)],
        )

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "packaging" / "windows" / "verify_runtime_db.py"),
            str(database),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "integrity=ok;foreign_key_violations=0;branches=4"
