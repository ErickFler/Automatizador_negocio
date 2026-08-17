from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from urllib.parse import quote

from app.db.database import get_db
from app.presentation.templates import templates
from app.services.dashboard_service import METRIC_LABELS
from app.services.export_service import (
    EXCEL_MEDIA_TYPE,
    ExcelExport,
    export_branches_workbook,
    export_daily_workbook,
    export_monthly_workbook,
    export_product_detail_workbook,
    export_products_workbook,
    export_weekly_history_workbook,
    export_weekly_workbook,
)
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


def _download(export: ExcelExport) -> Response:
    encoded = quote(export.filename)
    return Response(
        content=export.content,
        media_type=EXCEL_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f"attachment; filename=report.xlsx; filename*=UTF-8''{encoded}"
            ),
            "Content-Length": str(len(export.content)),
        },
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


@router.get("/daily/export")
def daily_export(
    selected_date: str = Query("", alias="date"),
    branch: str = "",
    category: str = "",
    product: str = "",
    session: Session = Depends(get_db),
) -> Response:
    report = build_daily_report(
        session,
        selected_date=selected_date,
        branch=branch,
        category=category,
        product=product,
    )
    return _download(export_daily_workbook(report))


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


@router.get("/weekly/export")
def weekly_export(
    selected_date: str = Query("", alias="date"),
    branch: str = "",
    category: str = "",
    product: str = "",
    sort: str = "sales_amount",
    direction: str = "desc",
    session: Session = Depends(get_db),
) -> Response:
    report = build_weekly_report(
        session,
        selected_date=selected_date,
        branch=branch,
        category=category,
        product=product,
        sort_by=sort,
        direction=direction,
    )
    return _download(export_weekly_workbook(report))


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


@router.get("/weekly-history/export")
def weekly_history_export(
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    session: Session = Depends(get_db),
) -> Response:
    report = build_weekly_history_report(
        session,
        year=year,
        branch=branch,
        category=category,
        product=product,
        metric=metric,
    )
    return _download(export_weekly_history_workbook(report))


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


@router.get("/monthly/export")
def monthly_export(
    year: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    session: Session = Depends(get_db),
) -> Response:
    report = build_monthly_report(
        session,
        year=year,
        branch=branch,
        category=category,
        product=product,
        metric=metric,
    )
    return _download(export_monthly_workbook(report))


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


@router.get("/products/export")
def products_export(
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    sort: str = "sales_amount",
    direction: str = "desc",
    session: Session = Depends(get_db),
) -> Response:
    report = build_products_report(
        session,
        date_from=date_from,
        date_to=date_to,
        branch=branch,
        category=category,
        sort_by=sort,
        direction=direction,
    )
    return _download(export_products_workbook(report))


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


@router.get("/products/{master_product_id}/export")
def product_detail_export(
    master_product_id: int,
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    session: Session = Depends(get_db),
) -> Response:
    report = build_product_detail_report(
        session,
        master_product_id=master_product_id,
        date_from=date_from,
        date_to=date_to,
        branch=branch,
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return _download(export_product_detail_workbook(report))


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


@router.get("/branches/export")
def branches_export(
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    product: str = "",
    metric: str = "sales_amount",
    session: Session = Depends(get_db),
) -> Response:
    report = build_branches_report(
        session,
        date_from=date_from,
        date_to=date_to,
        category=category,
        product=product,
        metric=metric,
    )
    return _download(export_branches_workbook(report))
