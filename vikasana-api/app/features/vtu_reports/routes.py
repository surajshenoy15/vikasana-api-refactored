from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

from app.core.dependencies import (
    get_current_admin,
    get_current_enabled_website_faculty_scope,
)

from app.features.auth.models import Admin

from app.features.faculty.permission_scope import (
    WebsiteFacultyScope,
)

from app.features.faculty.role_policy import (
    ROLE_COLLEGE_COORDINATOR,
    ROLE_HOD,
)

from app.features.vtu_reports.service import (
    build_csv,
    build_vtu_report,
    build_xlsx,
    get_admin_colleges,
    get_college_report_options,
    safe_filename,
    validate_report_scope,
)


# ============================================================
# ROUTERS
# ============================================================

admin_router = APIRouter(
    prefix="/admin/vtu-reports",
    tags=["Admin - VTU Reports"],
)

website_router = APIRouter(
    prefix="/website/faculty/vtu-reports",
    tags=["Website Faculty - VTU Reports"],
)


# ============================================================
# STATUS VALIDATION
# ============================================================

def normalize_status(value: str | None) -> str:

    value = str(
        value or "ALL"
    ).strip().upper()

    allowed = {
        "ALL",
        "READY",
        "NEEDS_MAPPING",
        "IN_PROGRESS",
    }

    if value not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Invalid VTU report status",
        )

    return value


# ============================================================
# COLLEGE COORDINATOR / HOD SCOPE
# ============================================================

async def resolve_website_report_scope(
    db: AsyncSession,
    *,
    scope: WebsiteFacultyScope,
    requested_department_id: int | None,
    batch_id: int | None,
) -> tuple[str, int]:

    if scope.role not in {
        ROLE_COLLEGE_COORDINATOR,
        ROLE_HOD,
    }:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only College Coordinator and HOD "
                "can access VTU reports"
            ),
        )

    # ========================================================
    # COLLEGE COORDINATOR
    #
    # Own college automatically.
    # Department selection is mandatory.
    # ========================================================

    if scope.role == ROLE_COLLEGE_COORDINATOR:

        if requested_department_id is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Select a department before "
                    "viewing or exporting the VTU report"
                ),
            )

        await validate_report_scope(
            db,
            college=scope.college,
            department_id=requested_department_id,
            batch_id=batch_id,
        )

        return (
            scope.college,
            requested_department_id,
        )

    # ========================================================
    # HOD
    #
    # Department always comes from authenticated HOD scope.
    # ========================================================

    if scope.department_id is None:
        raise HTTPException(
            status_code=409,
            detail="HOD department is not assigned",
        )

    if (
        requested_department_id is not None
        and requested_department_id
        != scope.department_id
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "HOD can access only "
                "their own department"
            ),
        )

    await validate_report_scope(
        db,
        college=scope.college,
        department_id=scope.department_id,
        batch_id=batch_id,
    )

    return (
        scope.college,
        scope.department_id,
    )


# ============================================================
# ADMIN OPTIONS
#
# Admin can select COLLEGE only.
# ============================================================

@admin_router.get("/options")
async def admin_vtu_report_options(
    db: AsyncSession = Depends(get_db),
    _: Admin = Depends(get_current_admin),
):
    colleges = await get_admin_colleges(db)

    return {
        "scope": "COLLEGE",
        "colleges": colleges,
    }


# ============================================================
# ADMIN PREVIEW
#
# Entire selected college.
# No department parameter.
# ============================================================

@admin_router.get("/preview")
async def admin_vtu_report_preview(

    college: str = Query(
        ...,
        min_length=1,
    ),

    batch_id: int | None = Query(
        None,
        ge=1,
    ),

    current_year: int | None = Query(
        None,
        ge=1,
        le=8,
    ),

    q: str | None = Query(None),

    report_status: str = Query(
        "ALL",
        alias="status",
    ),

    db: AsyncSession = Depends(get_db),

    _: Admin = Depends(get_current_admin),
):

    return await build_vtu_report(
        db,
        college=college,
        department_id=None,
        batch_id=batch_id,
        current_year=current_year,
        q=q,
        status_filter=normalize_status(
            report_status
        ),
    )


# ============================================================
# ADMIN CSV EXPORT
# ============================================================

@admin_router.get("/export.csv")
async def admin_vtu_report_export_csv(

    college: str = Query(
        ...,
        min_length=1,
    ),

    batch_id: int | None = Query(
        None,
        ge=1,
    ),

    current_year: int | None = Query(
        None,
        ge=1,
        le=8,
    ),

    report_status: str = Query(
        "ALL",
        alias="status",
    ),

    db: AsyncSession = Depends(get_db),

    _: Admin = Depends(get_current_admin),
):

    report = await build_vtu_report(
        db,
        college=college,
        department_id=None,
        batch_id=batch_id,
        current_year=current_year,
        status_filter=normalize_status(
            report_status
        ),
    )

    content = build_csv(
        report["items"]
    )

    filename = (
        "VTU_Activity_Points_"
        f"{safe_filename(college)}.csv"
    )

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
        },
    )


# ============================================================
# ADMIN EXCEL EXPORT
#
# ready_only=True by default.
# ============================================================

@admin_router.get("/export.xlsx")
async def admin_vtu_report_export_xlsx(

    college: str = Query(
        ...,
        min_length=1,
    ),

    batch_id: int | None = Query(
        None,
        ge=1,
    ),

    current_year: int | None = Query(
        None,
        ge=1,
        le=8,
    ),

    ready_only: bool = Query(True),

    db: AsyncSession = Depends(get_db),

    _: Admin = Depends(get_current_admin),
):

    report = await build_vtu_report(
        db,
        college=college,
        department_id=None,
        batch_id=batch_id,
        current_year=current_year,
        status_filter=(
            "READY"
            if ready_only
            else "ALL"
        ),
    )

    content = build_xlsx(
        report["items"]
    )

    filename = (
        "VTU_Activity_Points_"
        f"{safe_filename(college)}.xlsx"
    )

    return Response(
        content=content,
        media_type=(
            "application/"
            "vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
        },
    )


# ============================================================
# COLLEGE COORDINATOR / HOD OPTIONS
# ============================================================

@website_router.get("/options")
async def website_vtu_report_options(

    db: AsyncSession = Depends(get_db),

    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):

    if scope.role not in {
        ROLE_COLLEGE_COORDINATOR,
        ROLE_HOD,
    }:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only College Coordinator and HOD "
                "can access VTU reports"
            ),
        )

    options = await get_college_report_options(
        db,
        college=scope.college,
    )

    response = {
        "role": scope.role,
        "college": scope.college,
        "departments": options["departments"],
        "batches": options["batches"],
        "locked_department_id": None,
    }

    # HOD sees only own department.
    if scope.role == ROLE_HOD:

        if scope.department_id is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "HOD department is not assigned"
                ),
            )

        response["departments"] = [
            department
            for department
            in options["departments"]
            if department["id"]
            == scope.department_id
        ]

        response[
            "locked_department_id"
        ] = scope.department_id

    return response


# ============================================================
# COLLEGE COORDINATOR / HOD PREVIEW
# ============================================================

@website_router.get("/preview")
async def website_vtu_report_preview(

    department_id: int | None = Query(
        None,
        ge=1,
    ),

    batch_id: int | None = Query(
        None,
        ge=1,
    ),

    current_year: int | None = Query(
        None,
        ge=1,
        le=8,
    ),

    q: str | None = Query(None),

    report_status: str = Query(
        "ALL",
        alias="status",
    ),

    db: AsyncSession = Depends(get_db),

    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):

    college, resolved_department_id = (
        await resolve_website_report_scope(
            db,
            scope=scope,
            requested_department_id=department_id,
            batch_id=batch_id,
        )
    )

    return await build_vtu_report(
        db,
        college=college,
        department_id=resolved_department_id,
        batch_id=batch_id,
        current_year=current_year,
        q=q,
        status_filter=normalize_status(
            report_status
        ),
    )


# ============================================================
# COLLEGE COORDINATOR / HOD CSV
# ============================================================

@website_router.get("/export.csv")
async def website_vtu_report_export_csv(

    department_id: int | None = Query(
        None,
        ge=1,
    ),

    batch_id: int | None = Query(
        None,
        ge=1,
    ),

    current_year: int | None = Query(
        None,
        ge=1,
        le=8,
    ),

    report_status: str = Query(
        "ALL",
        alias="status",
    ),

    db: AsyncSession = Depends(get_db),

    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):

    college, resolved_department_id = (
        await resolve_website_report_scope(
            db,
            scope=scope,
            requested_department_id=department_id,
            batch_id=batch_id,
        )
    )

    report = await build_vtu_report(
        db,
        college=college,
        department_id=resolved_department_id,
        batch_id=batch_id,
        current_year=current_year,
        status_filter=normalize_status(
            report_status
        ),
    )

    content = build_csv(
        report["items"]
    )

    filename = (
        "VTU_Activity_Points_"
        f"{safe_filename(college)}_"
        f"Department_{resolved_department_id}"
        ".csv"
    )

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
        },
    )


# ============================================================
# COLLEGE COORDINATOR / HOD EXCEL
# ============================================================

@website_router.get("/export.xlsx")
async def website_vtu_report_export_xlsx(

    department_id: int | None = Query(
        None,
        ge=1,
    ),

    batch_id: int | None = Query(
        None,
        ge=1,
    ),

    current_year: int | None = Query(
        None,
        ge=1,
        le=8,
    ),

    ready_only: bool = Query(True),

    db: AsyncSession = Depends(get_db),

    scope: WebsiteFacultyScope = Depends(
        get_current_enabled_website_faculty_scope
    ),
):

    college, resolved_department_id = (
        await resolve_website_report_scope(
            db,
            scope=scope,
            requested_department_id=department_id,
            batch_id=batch_id,
        )
    )

    report = await build_vtu_report(
        db,
        college=college,
        department_id=resolved_department_id,
        batch_id=batch_id,
        current_year=current_year,
        status_filter=(
            "READY"
            if ready_only
            else "ALL"
        ),
    )

    content = build_xlsx(
        report["items"]
    )

    filename = (
        "VTU_Activity_Points_"
        f"{safe_filename(college)}_"
        f"Department_{resolved_department_id}"
        ".xlsx"
    )

    return Response(
        content=content,
        media_type=(
            "application/"
            "vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
        },
    )
