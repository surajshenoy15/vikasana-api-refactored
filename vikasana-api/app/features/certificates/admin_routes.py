from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

import csv
import io

from app.core.database import get_db
from app.core.dependencies import get_current_admin

from app.features.certificates.models import Certificate
from app.features.students.models import Student
from app.features.activities.models import ActivityType
from app.features.events.models import Event, EventSubmission

from app.core.cert_storage import presign_certificate_download_url  # ✅ you already have this

router = APIRouter(prefix="/admin/certificates", tags=["Admin - Certificates"])


@router.get("")
async def list_certificates(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    total = (await db.execute(select(func.count(Certificate.id)))).scalar() or 0

    stmt = (
        select(
            Certificate.id,
            Certificate.certificate_no,
            Certificate.issued_at,
            Certificate.pdf_path,
            Student.name.label("student"),
            ActivityType.name.label("category"),
            Event.title.label("event_title"),
        )
        .select_from(Certificate)
        .join(Student, Student.id == Certificate.student_id)
        .join(ActivityType, ActivityType.id == Certificate.activity_type_id)
        .join(Event, Event.id == Certificate.event_id)
        .order_by(Certificate.issued_at.desc(), Certificate.id.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.execute(stmt)).all()

    items = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "certificate_no": r.certificate_no,
                "student": r.student,
                "category": r.category,
                "title": r.event_title or "Event",
                "submittedOn": r.issued_at.isoformat() if r.issued_at else None,
                "pdf_url": None,  # keep null; download via /{id}/download-url
            }
        )

    return {"total": int(total), "items": items}


@router.get("/student-progress")
async def student_progress(
    limit: int = Query(60, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    # activities = total event submissions
    sub_stmt = (
        select(
            Student.id.label("id"),
            Student.name.label("name"),
            Student.college.label("college"),
            func.count(EventSubmission.id).label("activities"),
        )
        .select_from(Student)
        .join(EventSubmission, EventSubmission.student_id == Student.id, isouter=True)
        .group_by(Student.id, Student.name, Student.college)
        .order_by(func.count(EventSubmission.id).desc())
        .limit(limit)
    )
    subs = (await db.execute(sub_stmt)).all()
    student_ids = [r.id for r in subs]

    cert_map = {}
    if student_ids:
        cert_stmt = (
            select(Certificate.student_id, func.count(Certificate.id))
            .where(Certificate.student_id.in_(student_ids))
            .group_by(Certificate.student_id)
        )
        cert_rows = (await db.execute(cert_stmt)).all()
        cert_map = {sid: int(cnt or 0) for sid, cnt in cert_rows}

    students = []
    for r in subs:
        students.append(
            {
                "id": r.id,
                "name": r.name,
                "college": r.college,
                "activities": int(r.activities or 0),
                "certificates": int(cert_map.get(r.id, 0)),
            }
        )

    return {"students": students}


@router.get("/{certificate_id}/download-url")
async def certificate_download_url(
    certificate_id: int,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    cert = (
        await db.execute(select(Certificate).where(Certificate.id == certificate_id))
    ).scalar_one_or_none()

    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")

    if not cert.pdf_path:
        raise HTTPException(status_code=400, detail="Certificate PDF not generated")

    url = presign_certificate_download_url(cert.pdf_path)
    return {"url": url}


@router.get("/export")
async def export_certificates_csv(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    stmt = (
        select(
            Certificate.certificate_no,
            Certificate.issued_at,
            Student.name.label("student"),
            Student.usn.label("usn"),
            Student.college.label("college"),
            Event.title.label("event"),
            ActivityType.name.label("activity_type"),
        )
        .select_from(Certificate)
        .join(Student, Student.id == Certificate.student_id)
        .join(Event, Event.id == Certificate.event_id)
        .join(ActivityType, ActivityType.id == Certificate.activity_type_id)
        .order_by(Certificate.issued_at.desc(), Certificate.id.desc())
    )

    rows = (await db.execute(stmt)).all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["certificate_no", "issued_at", "student", "usn", "college", "event", "activity_type"])

    for r in rows:
        w.writerow(
            [
                r.certificate_no,
                r.issued_at.isoformat() if r.issued_at else "",
                r.student,
                r.usn or "",
                r.college or "",
                r.event or "",
                r.activity_type or "",
            ]
        )

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=certificates.csv"},
    )