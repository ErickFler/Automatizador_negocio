from fastapi.templating import Jinja2Templates

from app.core.config import RESOURCE_ROOT
from app.presentation.formatters import (
    format_date_mx,
    format_decimal,
    format_money,
    format_percent,
)


def asset_version(asset_path: str) -> str:
    path = RESOURCE_ROOT / "app" / "static" / asset_path.lstrip("/")
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return "0"

templates = Jinja2Templates(directory=RESOURCE_ROOT / "app" / "templates")
templates.env.filters["decimal"] = format_decimal
templates.env.filters["money"] = format_money
templates.env.filters["date_mx"] = format_date_mx
templates.env.filters["percent"] = format_percent
templates.env.globals["asset_version"] = asset_version
