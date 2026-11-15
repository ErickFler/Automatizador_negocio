from datetime import date, timedelta
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import DATA_ROOT, settings
from app.db.database import get_db
from app.db.models import Branch, ImportBatch, ProcessingIssue, RawSaleRecord
from app.domain.normalization import normalize_for_comparison
from app.presentation.templates import templates
from app.services.dashboard_service import (
    METRIC_LABELS,
    PERIODS,
    DashboardInputError,
    build_dashboard,
    with_input_error,
)
from app.services.duplicate_service import DuplicateImportError
from app.services.file_storage import calculate_sha256
from app.services.product_catalog import list_products_for_inspection
from app.services.product_mapping import (
    create_and_map_product,
    list_exact_mapping_candidates,
    list_pending_products,
    map_exact_product_matches,
    map_to_existing_product,
)
from app.services.product_matching import (
    find_product_candidates,
    search_master_products,
)
from app.services.raw_import_service import (
    FAILED,
    WAITING_FOR_PRODUCT_MAPPING,
    import_raw_sales_file,
)
from app.services.sales_processing_service import (
    get_batch_sales_summary,
    process_normalized_sales_batch,
)

router = APIRouter()
UPLOAD_DIRECTORY = DATA_ROOT / "uploads"
STATUS_LABELS = {
    "PENDING": "Pendiente",
    "PROCESSING": "Procesando",
    "COMPLETED": "Completada",
    "COMPLETED_WITH_WARNINGS": "Procesada con observaciones",
    "WAITING_FOR_PRODUCT_MAPPING": "Productos por confirmar",
    "FAILED": "Fallida",
}
WEEKLY_FILENAME_DATE = re.compile(r"(?P<year>20\d{2})[-_](?P<month>\d{2})[-_](?P<day>\d{2})")


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    period: str = "this_month",
    date_from: str = "",
    date_to: str = "",
    branch: str = "",
    category: str = "",
    product: str = "",
    metric: str = "total_units",
    sort: str = "sales_amount",
    direction: str = "desc",
    session: Session = Depends(get_db),
) -> HTMLResponse:
    status_code = status.HTTP_200_OK
    try:
        dashboard = build_dashboard(
            session,
            period=period,
            date_from=date_from,
            date_to=date_to,
            branch=branch,
            category=category,
            product=product,
            metric=metric,
            sort_by=sort,
            direction=direction,
        )
    except DashboardInputError as exc:
        dashboard = with_input_error(build_dashboard(session), str(exc))
        status_code = status.HTTP_400_BAD_REQUEST
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "dashboard": dashboard,
            "periods": PERIODS,
            "metric_labels": METRIC_LABELS,
        },
        status_code=status_code,
    )


@router.get("/products", response_class=HTMLResponse)
def products(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    branches = session.scalars(select(Branch).order_by(Branch.id)).all()
    catalog = list_products_for_inspection(session)
    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={"branches": branches, "products": catalog},
    )


@router.get("/imports/new", response_class=HTMLResponse)
def new_import(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    return _render_import_form(request, session)


@router.post("/imports", response_class=HTMLResponse)
def create_import(
    request: Request,
    sales_date: Annotated[date, Form()],
    branch_id: Annotated[list[int], Form()],
    file: Annotated[list[UploadFile], File()],
    replace_existing: Annotated[list[str] | None, Form()] = None,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    replacement_flags = _replacement_flags(replace_existing, len(file))
    selected_uploads = [
        (selected_branch_id, uploaded_file, replacement_flags[index])
        for index, (selected_branch_id, uploaded_file) in enumerate(
            zip(branch_id, file, strict=False)
        )
        if Path(uploaded_file.filename or "").name
    ]
    for uploaded_file in file:
        if not Path(uploaded_file.filename or "").name:
            uploaded_file.file.close()

    if (
        not selected_uploads
        or len(file) != len(branch_id)
        or len(selected_uploads) > 4
    ):
        return _render_import_form(
            request,
            session,
            error="Selecciona por lo menos un archivo, con un máximo de cuatro.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    selected_branch_ids = [item[0] for item in selected_uploads]
    if len(set(selected_branch_ids)) != len(selected_branch_ids):
        return _render_import_form(
            request,
            session,
            error="Cada sucursal puede aparecer una sola vez en la carga.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    branches = session.scalars(
        select(Branch).where(
            Branch.id.in_(selected_branch_ids),
            Branch.active.is_(True),
        )
    ).all()
    branches_by_id = {branch.id: branch for branch in branches}
    if len(branches_by_id) != len(selected_branch_ids):
        return _render_import_form(
            request,
            session,
            error="Selecciona únicamente sucursales disponibles.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    outcomes = [
        _process_uploaded_sales_file(
            session,
            uploaded_file=uploaded_file,
            branch=branches_by_id[selected_branch_id],
            sales_date=sales_date,
            replace_existing=replace_slot,
        )
        for selected_branch_id, uploaded_file, replace_slot in selected_uploads
    ]

    if len(outcomes) == 1 and outcomes[0]["batch_id"] is not None:
        return _render_import_result(
            request,
            session,
            batch_id=int(outcomes[0]["batch_id"]),
        )

    return templates.TemplateResponse(
        request=request,
        name="import_multi_result.html",
        context={
            "sales_date": sales_date,
            "outcomes": outcomes,
            "successful_count": sum(bool(item["successful"]) for item in outcomes),
            "pending_count": sum(bool(item["mapping_required"]) for item in outcomes),
        },
    )


@router.post("/imports/weekly", response_class=HTMLResponse)
def create_weekly_import(
    request: Request,
    week_date: Annotated[date, Form()],
    files: Annotated[list[UploadFile], File()],
    slot_date: Annotated[list[date] | None, Form()] = None,
    slot_branch_id: Annotated[list[int] | None, Form()] = None,
    replace_existing: Annotated[list[str] | None, Form()] = None,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    selected_files = [
        uploaded_file
        for uploaded_file in files
        if Path(uploaded_file.filename or "").name
    ]
    if not selected_files or len(selected_files) > 28:
        for uploaded_file in files:
            uploaded_file.file.close()
        return _render_import_form(
            request,
            session,
            error="Selecciona entre 1 y 28 archivos para la semana.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    branches = session.scalars(
        select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
    ).all()
    branches_by_id = {branch.id: branch for branch in branches}
    week_start = week_date - timedelta(days=week_date.weekday())
    week_end = week_start + timedelta(days=6)
    detected: set[tuple[date, int]] = set()
    outcomes: list[dict[str, object]] = []
    explicit_slots = slot_date is not None or slot_branch_id is not None
    if explicit_slots and (
        slot_date is None
        or slot_branch_id is None
        or len(slot_date) != len(selected_files)
        or len(slot_branch_id) != len(selected_files)
    ):
        for uploaded_file in selected_files:
            uploaded_file.file.close()
        return _render_import_form(
            request,
            session,
            error="No pudimos relacionar todos los archivos con su día y sucursal.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    replacement_flags = _replacement_flags(replace_existing, len(selected_files))
    for index, uploaded_file in enumerate(selected_files):
        filename = Path(uploaded_file.filename or "").name
        metadata = (
            (
                slot_date[index],
                branches_by_id.get(slot_branch_id[index]),
            )
            if explicit_slots and slot_date is not None and slot_branch_id is not None
            else _detect_weekly_file_metadata(filename=filename, branches=branches)
        )
        if metadata is not None and metadata[1] is None:
            metadata = None
        if metadata is None:
            uploaded_file.file.close()
            outcomes.append(
                _unprocessed_outcome(
                    filename=filename,
                    branch="No identificada",
                    message="No pudimos identificar la fecha o la sucursal en el nombre.",
                )
            )
            continue
        detected_date, detected_branch = metadata
        key = (detected_date, detected_branch.id)
        if not week_start <= detected_date <= week_end:
            uploaded_file.file.close()
            outcomes.append(
                _unprocessed_outcome(
                    filename=filename,
                    branch=detected_branch.name,
                    sales_date=detected_date,
                    message="La fecha del archivo no pertenece a la semana seleccionada.",
                )
            )
            continue
        if key in detected:
            uploaded_file.file.close()
            outcomes.append(
                _unprocessed_outcome(
                    filename=filename,
                    branch=detected_branch.name,
                    sales_date=detected_date,
                    message="Ya seleccionaste otro archivo para ese día y sucursal.",
                )
            )
            continue
        detected.add(key)
        outcomes.append(
            _process_uploaded_sales_file(
                session,
                uploaded_file=uploaded_file,
                branch=detected_branch,
                sales_date=detected_date,
                replace_existing=replacement_flags[index],
            )
        )

    return _render_weekly_result(
        request,
        week_start=week_start,
        week_end=week_end,
        outcomes=outcomes,
    )


def _process_uploaded_sales_file(
    session: Session,
    *,
    uploaded_file: UploadFile,
    branch: Branch,
    sales_date: date,
    replace_existing: bool = False,
) -> dict[str, object]:
    original_filename = Path(uploaded_file.filename or "").name
    try:
        with TemporaryDirectory(prefix="raw-sales-import-") as temporary_directory:
            temporary_path = Path(temporary_directory) / (
                f"reporte{Path(original_filename).suffix.lower()}"
            )
            with temporary_path.open("wb") as destination:
                shutil.copyfileobj(uploaded_file.file, destination)
            existing_batches = session.scalars(
                select(ImportBatch).where(
                    ImportBatch.sales_date == sales_date,
                    ImportBatch.branch_id == branch.id,
                )
            ).all()
            incoming_sha256 = calculate_sha256(temporary_path)
            if any(batch.sha256 == incoming_sha256 for batch in existing_batches):
                return _unprocessed_outcome(
                    filename=original_filename,
                    branch=branch.name,
                    sales_date=sales_date,
                    status_label="Duplicado",
                    message=(
                        "Este mismo archivo ya fue cargado para la sucursal y fecha."
                    ),
                )
            if existing_batches and not replace_existing:
                return _unprocessed_outcome(
                    filename=original_filename,
                    branch=branch.name,
                    sales_date=sales_date,
                    status_label="Ya existe una carga",
                    message=(
                        "Para evitar ventas duplicadas, marque Reemplazar carga existente "
                        "antes de procesar este archivo."
                    ),
                )
            result = import_raw_sales_file(
                session,
                file_path=temporary_path,
                original_filename=original_filename,
                sales_date=sales_date,
                branch=branch,
                upload_directory=UPLOAD_DIRECTORY,
            )
            if result.status not in {WAITING_FOR_PRODUCT_MAPPING, FAILED}:
                process_normalized_sales_batch(session, batch_id=result.batch_id)
            batch = session.get(ImportBatch, result.batch_id)
            final_status = batch.status if batch is not None else result.status
            if existing_batches and replace_existing and batch is not None:
                if final_status in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}:
                    _delete_import_batches(session, existing_batches)
                else:
                    _delete_import_batches(session, [batch])
                    return _unprocessed_outcome(
                        filename=original_filename,
                        branch=branch.name,
                        sales_date=sales_date,
                        status_label="No reemplazada",
                        message=(
                            "La carga anterior se conservó porque el archivo nuevo "
                            "necesita revisión."
                        ),
                    )
    except DuplicateImportError as exc:
        session.rollback()
        return _unprocessed_outcome(
            filename=original_filename,
            branch=branch.name,
            sales_date=sales_date,
            status_label="Duplicado",
            message=str(exc),
        )
    except (OSError, ValueError):
        session.rollback()
        return _unprocessed_outcome(
            filename=original_filename,
            branch=branch.name,
            sales_date=sales_date,
            message="No fue posible conservar o procesar el archivo.",
        )
    finally:
        uploaded_file.file.close()

    if batch is None:
        return _unprocessed_outcome(
            filename=original_filename,
            branch=branch.name,
            sales_date=sales_date,
            message="No fue posible recuperar el resultado de la carga.",
        )
    if final_status == WAITING_FOR_PRODUCT_MAPPING:
        message = "Hay productos nuevos que necesitan confirmación."
    elif final_status == FAILED:
        message = "El archivo tiene un problema que impide procesarlo."
    elif batch.skipped_rows:
        message = (
            f"{batch.normalized_rows} filas incluidas y "
            f"{batch.skipped_rows} para revisar."
        )
    else:
        message = f"{batch.normalized_rows} filas incluidas."
    return {
        "batch_id": batch.id,
        "filename": original_filename,
        "branch": branch.name,
        "sales_date": sales_date,
        "status_label": STATUS_LABELS[final_status],
        "successful": final_status != FAILED,
        "mapping_required": final_status == WAITING_FOR_PRODUCT_MAPPING,
        "message": message,
        "normalized_rows": batch.normalized_rows,
        "skipped_rows": batch.skipped_rows,
        "total_rows": batch.total_rows,
    }


def _unprocessed_outcome(
    *,
    filename: str,
    branch: str,
    message: str,
    sales_date: date | None = None,
    status_label: str = "No procesado",
) -> dict[str, object]:
    return {
        "batch_id": None,
        "filename": filename,
        "branch": branch,
        "sales_date": sales_date,
        "status_label": status_label,
        "successful": False,
        "mapping_required": False,
        "message": message,
        "normalized_rows": 0,
        "skipped_rows": 0,
        "total_rows": 0,
    }


def _replacement_flags(values: list[str] | None, count: int) -> list[bool]:
    normalized = [
        str(value).strip().casefold() in {"1", "true", "yes", "si", "sí", "on"}
        for value in (values or [])
    ]
    return (normalized + [False] * count)[:count]


def _delete_import_batches(
    session: Session, batches: list[ImportBatch]
) -> None:
    stored_files = [UPLOAD_DIRECTORY / batch.stored_filename for batch in batches]
    for batch in batches:
        session.delete(batch)
    session.commit()
    for stored_file in stored_files:
        stored_file.unlink(missing_ok=True)


def _render_weekly_result(
    request: Request,
    *,
    week_start: date,
    week_end: date,
    outcomes: list[dict[str, object]],
) -> HTMLResponse:
    sorted_outcomes = sorted(
        outcomes,
        key=lambda item: (
            item.get("sales_date") or date.max,
            str(item.get("branch")),
        ),
    )
    grouped: dict[date | None, list[dict[str, object]]] = {}
    for outcome in sorted_outcomes:
        outcome_date = outcome.get("sales_date")
        grouped.setdefault(
            outcome_date if isinstance(outcome_date, date) else None, []
        ).append(outcome)
    outcome_days = [
        {
            "sales_date": outcome_date,
            "outcomes": day_outcomes,
            "successful_count": sum(
                bool(item["successful"]) for item in day_outcomes
            ),
            "problem_count": sum(
                not bool(item["successful"]) for item in day_outcomes
            ),
        }
        for outcome_date, day_outcomes in grouped.items()
    ]
    return templates.TemplateResponse(
        request=request,
        name="import_week_result.html",
        context={
            "week_start": week_start,
            "week_end": week_end,
            "outcomes": sorted_outcomes,
            "outcome_days": outcome_days,
            "received_count": sum(
                bool(item["successful"]) for item in sorted_outcomes
            ),
            "pending_count": sum(
                bool(item["mapping_required"]) for item in sorted_outcomes
            ),
            "problem_count": sum(
                not bool(item["successful"]) for item in sorted_outcomes
            ),
        },
    )


def _detect_weekly_file_metadata(
    *, filename: str, branches: list[Branch]
) -> tuple[date, Branch] | None:
    date_match = WEEKLY_FILENAME_DATE.search(filename)
    if date_match is None:
        return None
    try:
        detected_date = date(
            int(date_match.group("year")),
            int(date_match.group("month")),
            int(date_match.group("day")),
        )
    except ValueError:
        return None

    searchable_name = re.sub(
        r"[^\w]+",
        " ",
        normalize_for_comparison(Path(filename).stem).replace("_", " "),
    ).strip()
    aliases = {
        "Loma Linda": ("loma linda", "loma"),
        "Solidaridad": ("solidaridad", "soli"),
        "Alianza": ("alianza",),
        "Unidad": ("unidad", "uni"),
    }
    detected_branch = next(
        (
            branch
            for branch in branches
            if any(
                re.search(rf"(?:^|\s){re.escape(alias)}(?:\s|$)", searchable_name)
                for alias in aliases.get(
                    branch.name,
                    (normalize_for_comparison(branch.name),),
                )
            )
        ),
        None,
    )
    return (detected_date, detected_branch) if detected_branch is not None else None


@router.get("/imports/coverage")
def import_coverage(
    start_date: date,
    days: int = 1,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    safe_days = min(max(days, 1), 7)
    end_date = start_date + timedelta(days=safe_days - 1)
    batches = session.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.sales_date >= start_date,
            ImportBatch.sales_date <= end_date,
        )
        .order_by(ImportBatch.sales_date, ImportBatch.branch_id, ImportBatch.id.desc())
    ).all()
    latest_by_slot: dict[tuple[date, int], ImportBatch] = {}
    for batch in batches:
        latest_by_slot.setdefault((batch.sales_date, batch.branch_id), batch)
    return {
        "slots": [
            {
                "sales_date": batch.sales_date.isoformat(),
                "branch_id": batch.branch_id,
                "batch_id": batch.id,
                "status": batch.status,
                "status_label": STATUS_LABELS.get(batch.status, batch.status),
            }
            for batch in latest_by_slot.values()
        ]
    }


@router.get("/imports", response_class=HTMLResponse)
def import_history(
    request: Request, session: Session = Depends(get_db)
) -> HTMLResponse:
    batches = session.scalars(
        select(ImportBatch)
        .options(selectinload(ImportBatch.branch))
        .order_by(ImportBatch.sales_date.desc(), ImportBatch.branch_id)
        .limit(100)
    ).all()
    grouped: dict[date, list[ImportBatch]] = {}
    for batch in batches:
        grouped.setdefault(batch.sales_date, []).append(batch)
    history_days = [
        {
            "sales_date": sales_date,
            "batches": day_batches,
            "ready_count": sum(
                batch.status in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}
                for batch in day_batches
            ),
            "attention_count": sum(
                batch.status in {"WAITING_FOR_PRODUCT_MAPPING", "FAILED"}
                for batch in day_batches
            ),
        }
        for sales_date, day_batches in grouped.items()
    ]
    return templates.TemplateResponse(
        request=request,
        name="imports_history.html",
        context={
            "batches": batches,
            "history_days": history_days,
            "status_labels": STATUS_LABELS,
        },
    )


@router.get("/imports/{batch_id}", response_class=HTMLResponse)
def import_result(
    request: Request, batch_id: int, session: Session = Depends(get_db)
) -> HTMLResponse:
    return _render_import_result(request, session, batch_id=batch_id)


@router.get("/imports/{batch_id}/products", response_class=HTMLResponse)
def map_import_products(
    request: Request,
    batch_id: int,
    search: str = "",
    saved: bool = False,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    return _render_product_mapping(
        request,
        session,
        batch_id=batch_id,
        search=search,
        saved=saved,
    )


@router.post("/imports/{batch_id}/products/map")
def confirm_existing_product(
    request: Request,
    batch_id: int,
    external_product_id: Annotated[str, Form()],
    master_product_id: Annotated[int, Form()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        map_to_existing_product(
            session,
            batch_id=batch_id,
            external_product_id=external_product_id,
            master_product_id=master_product_id,
        )
    except ValueError as exc:
        session.rollback()
        return _render_product_mapping(
            request,
            session,
            batch_id=batch_id,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    return RedirectResponse(
        url=(
            f"/imports/{batch_id}/products?saved=true"
            if pending
            else f"/imports/{batch_id}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/imports/{batch_id}/products/map-exact")
def confirm_exact_products(
    request: Request,
    batch_id: int,
    session: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        result = map_exact_product_matches(session, batch_id=batch_id)
    except ValueError as exc:
        session.rollback()
        return _render_product_mapping(
            request,
            session,
            batch_id=batch_id,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    return RedirectResponse(
        url=(
            f"/imports/{batch_id}/products?saved=true"
            if pending
            else f"/imports/{batch_id}?mapped={result.mapped_count}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/imports/{batch_id}/products/create")
def confirm_new_product(
    request: Request,
    batch_id: int,
    external_product_id: Annotated[str, Form()],
    master_display_name: Annotated[str, Form()],
    category_display_name: Annotated[str, Form()],
    session: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        create_and_map_product(
            session,
            batch_id=batch_id,
            external_product_id=external_product_id,
            master_display_name=master_display_name,
            category_display_name=category_display_name,
        )
    except ValueError as exc:
        session.rollback()
        return _render_product_mapping(
            request,
            session,
            batch_id=batch_id,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    return RedirectResponse(
        url=(
            f"/imports/{batch_id}/products?saved=true"
            if pending
            else f"/imports/{batch_id}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _render_import_form(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    branches = session.scalars(
        select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="import_new.html",
        context={"branches": branches, "error": error},
        status_code=status_code,
    )


def _render_import_result(
    request: Request, session: Session, *, batch_id: int
) -> HTMLResponse:
    batch = session.scalar(
        select(ImportBatch)
        .options(
            selectinload(ImportBatch.branch),
            selectinload(ImportBatch.processing_issues).selectinload(
                ProcessingIssue.raw_sale_record
            ),
        )
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        return templates.TemplateResponse(
            request=request,
            name="import_result.html",
            context={"batch": None, "status_labels": STATUS_LABELS},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return templates.TemplateResponse(
        request=request,
        name="import_result.html",
        context={
            "batch": batch,
            "summary": get_batch_sales_summary(session, batch_id=batch_id),
            "status_labels": STATUS_LABELS,
            "issue_notice": _friendly_import_issue_notice(batch),
            "show_technical_details": settings.debug,
        },
    )


def _friendly_import_issue_notice(batch: ImportBatch) -> str:
    issue_types = {issue.issue_type for issue in batch.processing_issues}
    if issue_types == {"ZERO_SALE_PRICE"}:
        return (
            "El archivo contiene productos sin precio de venta. Esas filas se "
            "conservaron, pero no se incluyeron porque no es posible separar sus "
            "unidades de venta individual y combo."
        )
    return (
        "Algunos datos del archivo no pudieron calcularse. Las filas se conservaron "
        "para revisión y no se incluyeron en los totales procesados."
    )


def _render_product_mapping(
    request: Request,
    session: Session,
    *,
    batch_id: int,
    search: str = "",
    saved: bool = False,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    batch = session.scalar(
        select(ImportBatch)
        .options(selectinload(ImportBatch.branch))
        .where(ImportBatch.id == batch_id)
    )
    if batch is None:
        return templates.TemplateResponse(
            request=request,
            name="product_mapping.html",
            context={"batch": None, "pending": []},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    pending = list_pending_products(session, batch_id=batch_id)
    exact_matches = list_exact_mapping_candidates(session, batch_id=batch_id)
    current = pending[0] if pending else None
    suggestions = (
        find_product_candidates(
            session,
            original_name=current.original_product_name,
            original_category_name=current.original_category_name,
        )
        if current
        else []
    )
    manual_results = (
        search_master_products(session, query=search) if search.strip() else []
    )
    all_external_ids = {
        (external_id or "").strip()
        for external_id in session.scalars(
            select(RawSaleRecord.external_product_id).where(
                RawSaleRecord.import_batch_id == batch_id
            )
        ).all()
        if (external_id or "").strip()
    }
    resolved_count = len(all_external_ids) - len(pending)
    return templates.TemplateResponse(
        request=request,
        name="product_mapping.html",
        context={
            "batch": batch,
            "pending": pending,
            "exact_matches": exact_matches,
            "current": current,
            "suggestions": suggestions,
            "manual_results": manual_results,
            "search": search,
            "saved": saved,
            "error": error,
            "current_position": resolved_count + 1 if current else resolved_count,
            "total_products": len(all_external_ids),
        },
        status_code=status_code,
    )
