from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import RESOURCE_ROOT
from app.db.database import get_db
from app.db.models import Branch
from app.services.product_catalog import list_products_for_inspection

TEMPLATE_DIRECTORY = RESOURCE_ROOT / "app" / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIRECTORY)
router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/products", response_class=HTMLResponse)
def products(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    branches = session.scalars(select(Branch).order_by(Branch.id)).all()
    catalog = list_products_for_inspection(session)
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={"branches": branches, "products": catalog},
    )
