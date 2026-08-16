from pathlib import Path
import struct

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
        "version_info.txt",
        "build.ps1",
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
