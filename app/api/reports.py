from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.presentation.templates import templates
from app.services.dashboard_service import METRIC_LABELS
from app.services.reporting_service import (
    build_branches_report,
    build_daily_report,
    build_monthly_report,
    build_product_detail_report,
    build_products_report,
    build_weekly_history_report,
    build_weekly_report,
)

router = APIRouter(prefix="/reports", tags=["reports"])


def _render(request: Request, name: str, report: object | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=f"reports/{name}",
        context={"report": report, "metric_labels": METRIC_LABELS},
    )


@router.get("", response_class=HTMLResponse)
def reports_index(request: Request) -> HTMLResponse:
    return _render(request, "index.html")


@router.get("/daily", response_class=HTMLResponse)
def daily_report(
    request: Request,
    selected_date: str = Query("", alias="date"),
    branch: str = "",
    category: str = "",
    product: str = "",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render(
        request,
        "daily.html",
        build_daily_report(
            session,
            selected_date=selected_date,
            branch=branch,
            category=category,
            product=product,
        ),
    )


@router.get("/weekly", response_class=HTMLResponse)
def weekly_report(
    request: Request,
    selected_date: str = Query("", alias="date"),
    branch: str = "",
    category: str = "",
    product: str = "",
    sort: str = "sales_amount",
    direction: str = "desc",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render(
        request,
        "weekly.html",
        build_weekly_report(
            session,
            selected_date=selected_date,
            branch=branch,
            category=category,
            product=product,
            sort_by=sort,
            direction=direction,
        ),
    )


@router.get("/weekly-history", response_class=HTMLResponse)
def weekly_history_report(
    request: Request,
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render(
        request,
        "weekly_history.html",
        build_weekly_history_report(
            session,
            year=year,
            branch=branch,
            category=category,
            product=product,
            metric=metric,
        ),
    )


@router.get("/monthly", response_class=HTMLResponse)
def monthly_report(
    request: Request,
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render(
        request,
        "monthly.html",
        build_monthly_report(
            session,
            year=year,
            branch=branch,
            category=category,
            product=product,
            metric=metric,
        ),
    )


@router.get("/products", response_class=HTMLResponse)
def products_report(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    sort: str = "sales_amount",
    direction: str = "desc",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render(
        request,
        "products.html",
        build_products_report(
            session,
            date_from=date_from,
            date_to=date_to,
            branch=branch,
            category=category,
            sort_by=sort,
            direction=direction,
        ),
    )


@router.get("/products/{master_product_id}", response_class=HTMLResponse)
def product_detail_report(
    request: Request,
    master_product_id: int,
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    report = build_product_detail_report(
        session,
        master_product_id=master_product_id,
        date_from=date_from,
        date_to=date_to,
        branch=branch,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return _render(request, "product_detail.html", report)


@router.get("/branches", response_class=HTMLResponse)
def branches_report(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render(
        request,
        "branches.html",
        build_branches_report(
            session,
            date_from=date_from,
            date_to=date_to,
            category=category,
            product=product,
            metric=metric,
        ),
    )
