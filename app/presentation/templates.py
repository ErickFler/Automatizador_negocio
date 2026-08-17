from fastapi.templating import Jinja2Templates

from app.core.config import RESOURCE_ROOT
from app.presentation.formatters import format_date_mx, format_decimal, format_money

templates = Jinja2Templates(directory=RESOURCE_ROOT / "app" / "templates")
templates.env.filters["decimal"] = format_decimal
templates.env.filters["money"] = format_money
templates.env.filters["date_mx"] = format_date_mx
