from pathlib import Path

from app.core.config import PROJECT_ROOT, default_data_root


def test_development_data_remains_inside_project() -> None:
    assert default_data_root(frozen=False) == PROJECT_ROOT / "data"


def test_frozen_windows_uses_local_app_data() -> None:
    result = default_data_root(
        frozen=True,
        platform_name="win32",
        environment={"LOCALAPPDATA": r"C:\Users\Prueba\AppData\Local"},
        home_directory=Path("/unused"),
    )

    assert result == Path(r"C:\Users\Prueba\AppData\Local") / "ConcentradoVentas"


def test_data_directory_can_be_overridden() -> None:
    result = default_data_root(
        frozen=True,
        platform_name="win32",
        environment={"APP_DATA_DIR": "/custom/data"},
        home_directory=Path("/unused"),
    )

    assert result == Path("/custom/data").resolve()
