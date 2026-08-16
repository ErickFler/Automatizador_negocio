import os
from dataclasses import dataclass
from pathlib import Path
import sys

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRANCHES = ("Loma Linda", "Solidaridad", "Alianza", "Unidad")
IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


def default_data_root(
    *,
    frozen: bool = IS_FROZEN,
    platform_name: str = sys.platform,
    environment: dict[str, str] | None = None,
    home_directory: Path | None = None,
) -> Path:
    selected_environment = environment if environment is not None else os.environ
    override = selected_environment.get("APP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if not frozen:
        return PROJECT_ROOT / "data"

    home = home_directory or Path.home()
    if platform_name == "win32":
        local_app_data = selected_environment.get("LOCALAPPDATA")
        base_directory = (
            Path(local_app_data)
            if local_app_data
            else home / "AppData" / "Local"
        )
        return base_directory / "ConcentradoVentas"
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "ConcentradoVentas"
    xdg_data_home = selected_environment.get("XDG_DATA_HOME")
    base_directory = Path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
    return base_directory / "concentrado-ventas"


DATA_ROOT = default_data_root()
RUNTIME_ROOT = DATA_ROOT if IS_FROZEN else PROJECT_ROOT
load_dotenv((DATA_ROOT if IS_FROZEN else PROJECT_ROOT) / ".env")


def _resolve_database_url(value: str) -> str:
    prefix = "sqlite:///"
    if not value.startswith(prefix):
        return value
    database_path = Path(value.removeprefix(prefix))
    if not database_path.is_absolute():
        database_path = RUNTIME_ROOT / database_path
    return f"{prefix}{database_path}"


def _load_branches(value: str) -> tuple[str, ...]:
    branches = tuple(branch.strip() for branch in value.split(",") if branch.strip())
    return branches or DEFAULT_BRANCHES


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Sistema de Concentrado de Ventas")
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8000"))
    debug: bool = os.getenv("APP_DEBUG", "false").lower() == "true"
    database_url: str = _resolve_database_url(
        os.getenv("DATABASE_URL", f"sqlite:///{DATA_ROOT / 'app.db'}")
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    branches: tuple[str, ...] = _load_branches(
        os.getenv(
            "APP_BRANCHES",
            ",".join(DEFAULT_BRANCHES),
        )
    )


settings = Settings()
