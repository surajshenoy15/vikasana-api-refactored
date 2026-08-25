from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityType
from app.features.students.models import Student
from app.features.activities.models import StudentActivityProgress
from app.features.activities.models import StudentActivityStats
from app.features.activities.models import StudentPointAdjustment


# ─────────────────────────────────────────────
# Existing auto-award logic
# ─────────────────────────────────────────────
async def award_points_for_session(
    db: AsyncSession,
    session_id: int,
    *,
    created_by_admin_id: int | None = None,
) -> dict:
    res = await db.execute(
        select(ActivitySession)
        .where(ActivitySession.id == session_id)
        .with_for_update()
    )
    session = res.scalar_one_or_none()
    if not session:
        raise ValueError("Session not found")

    if getattr(session, "points_awarded_at", None) is not None:
        return {"awarded": 0, "reason": "Points already awarded for this session"}

    # Event-generated sessions are scored by the event submission
    # cumulative scoring engine. Never award them again here.
    if getattr(session, "event_submission_id", None) is not None:
        return {
            "awarded": 0,
            "reason": "Event submission points are handled by event scoring",
        }

    if session.status not in {ActivitySessionStatus.SUBMITTED, ActivitySessionStatus.APPROVED}:
        return {"awarded": 0, "reason": f"Session status is {session.status}, not eligible"}

    q = select(ActivityPhoto).where(
        ActivityPhoto.session_id == session_id,
        ActivityPhoto.seq_no.in_([1, 5]),
    )
    rows = (await db.execute(q)).scalars().all()
    p1 = next((p for p in rows if p.seq_no == 1), None)
    p5 = next((p for p in rows if p.seq_no == 5), None)

    if not p1 or not p5:
        return {"awarded": 0, "reason": "Missing seq 1 or seq 5 photo"}

    t1 = p1.captured_at or p1.created_at
    t5 = p5.captured_at or p5.created_at

    if not t1 or not t5 or t5 <= t1:
        return {"awarded": 0, "reason": "Invalid timestamps for duration"}

    duration_minutes = int((t5 - t1).total_seconds() // 60)
    if duration_minutes <= 0:
        return {"awarded": 0, "reason": "Duration too small"}

    session.duration_hours = round(duration_minutes / 60.0, 2)

    activity_type = await db.get(ActivityType, session.activity_type_id)
    if not activity_type or not getattr(activity_type, "is_active", True):
        return {"awarded": 0, "reason": "Activity type not active"}

    unit_minutes = int(activity_type.hours_per_unit * 60)
    unit_points = int(activity_type.points_per_unit)
    max_points = int(activity_type.max_points)

    if unit_minutes <= 0 or unit_points <= 0:
        return {"awarded": 0, "reason": "Invalid activity rule config"}

    prog_q = (
        select(StudentActivityProgress)
        .where(
            StudentActivityProgress.student_id == session.student_id,
            StudentActivityProgress.activity_type_id == session.activity_type_id,
        )
        .with_for_update()
    )
    prog = (await db.execute(prog_q)).scalars().first()

    if not prog:
        prog = StudentActivityProgress(
            student_id=session.student_id,
            activity_type_id=session.activity_type_id,
            total_minutes=0,
            points_awarded=0,
        )
        db.add(prog)
        await db.flush()

    prog.total_minutes = int(prog.total_minutes or 0) + duration_minutes

    should_have = (prog.total_minutes // unit_minutes) * unit_points
    if should_have > max_points:
        should_have = max_points

    new_points = should_have - int(prog.points_awarded or 0)
    if new_points < 0:
        new_points = 0

    student_total = None

    if new_points > 0:
        stu_q = select(Student).where(Student.id == session.student_id).with_for_update()
        student = (await db.execute(stu_q)).scalars().first()
        if not student:
            raise ValueError("Student not found")

        student.total_points_earned = int(student.total_points_earned or 0) + int(new_points)
        student_total = int(student.total_points_earned)

        prog.points_awarded = int(should_have)

        db.add(
            StudentPointAdjustment(
                student_id=student.id,
                delta_points=int(new_points),
                new_total_points=student_total,
                reason=f"AUTO_AWARD_SESSION_{session.id}",
                created_by_admin_id=created_by_admin_id,
                activity_name=f"Session #{session.id}",
                category="Auto Award",
                status="approved",
                remarks=f"Auto awarded from session {session.id}",
            )
        )

    session.points_awarded_at = func.now()

    return {
        "awarded": int(new_points),
        "duration_minutes": int(duration_minutes),
        "total_minutes": int(prog.total_minutes),
        "points_awarded_total_for_activity": int(prog.points_awarded),
        "student_total_points": student_total,
    }


# ─────────────────────────────────────────────
# Shared Admin Set Points -> Activity Stats helper
# ─────────────────────────────────────────────
async def _apply_manual_activity_points_delta(
    db: AsyncSession,
    *,
    student_id: int,
    activity_type_id: int,
    delta_manual_points: int,
) -> int:
    """
    Apply a manual point change to the same cumulative activity stats
    used by automatic event scoring.

    Example with 4 hrs = 1 point:
        +5 manual points -> +20.00 cumulative hours
        -2 manual points -> -8.00 cumulative hours

    Returns the actual change in points_awarded for this activity type.
    """

    activity_type = await db.get(
        ActivityType,
        int(activity_type_id),
    )

    if not activity_type:
        raise ValueError("Activity type not found")

    if not getattr(activity_type, "is_active", True):
        raise ValueError("Activity type is not active")

    hpu = float(
        getattr(activity_type, "hours_per_unit", 0) or 0
    )
    ppu = int(
        getattr(activity_type, "points_per_unit", 0) or 0
    )
    max_points = min(
        20,
        int(
            getattr(activity_type, "max_points", 20)
            or 20
        ),
    )

    if hpu <= 0 or ppu <= 0:
        raise ValueError("Invalid activity rule config")

    stats_q = await db.execute(
        select(StudentActivityStats)
        .where(
            StudentActivityStats.student_id == student_id,
            StudentActivityStats.activity_type_id == activity_type_id,
        )
        .with_for_update()
    )

    stats = stats_q.scalar_one_or_none()

    if stats is None:
        stats = StudentActivityStats(
            student_id=student_id,
            activity_type_id=activity_type_id,
            total_verified_hours=0.0,
            points_awarded=0,
            completed_at=None,
        )
        db.add(stats)
        await db.flush()

    old_hours = float(
        stats.total_verified_hours or 0.0
    )
    old_points = int(
        stats.points_awarded or 0
    )

    equivalent_hours_delta = (
        float(delta_manual_points) *
        (hpu / float(ppu))
    )

    new_hours = max(
        0.0,
        old_hours + equivalent_hours_delta,
    )

    completed_units = int(
        new_hours // hpu
    )

    new_points = min(
        max_points,
        completed_units * ppu,
    )

    stats.total_verified_hours = new_hours
    stats.points_awarded = new_points

    if new_points < max_points:
        stats.completed_at = None

    return int(new_points - old_points)


# ─────────────────────────────────────────────
# Admin manual CRUD for point adjustments
# ─────────────────────────────────────────────
async def get_student_point_adjustments(
    db: AsyncSession,
    student_id: int,
) -> tuple[Student, list[StudentPointAdjustment]]:
    student = await db.get(Student, student_id)
    if not student:
        raise ValueError("Student not found")

    res = await db.execute(
        select(StudentPointAdjustment)
        .where(StudentPointAdjustment.student_id == student_id)
        .order_by(StudentPointAdjustment.created_at.desc(), StudentPointAdjustment.id.desc())
    )
    items = res.scalars().all()
    return student, items


async def create_student_point_adjustment(
    db: AsyncSession,
    *,
    student_id: int,
    activity_type_id: int,
    activity_name: str,
    category: str | None,
    points: int,
    date,
    status: str,
    remarks: str | None,
    created_by_admin_id: int | None,
) -> tuple[StudentPointAdjustment, int]:
    student_res = await db.execute(
        select(Student).where(Student.id == student_id).with_for_update()
    )
    student = student_res.scalar_one_or_none()
    if not student:
        raise ValueError("Student not found")

    # Ensure the selected activity type exists.
    activity_type = await db.get(
        ActivityType,
        int(activity_type_id),
    )
    if not activity_type:
        raise ValueError("Activity type not found")

    if not getattr(activity_type, "is_active", True):
        raise ValueError("Activity type is not active")

    # Only APPROVED manual entries contribute to cumulative progress.
    effective_manual_points = (
        int(points)
        if str(status or "").lower() == "approved"
        else 0
    )

    activity_points_delta = 0

    if effective_manual_points != 0:
        activity_points_delta = await _apply_manual_activity_points_delta(
            db,
            student_id=student.id,
            activity_type_id=int(activity_type_id),
            delta_manual_points=effective_manual_points,
        )

    new_total = (
        int(student.total_points_earned or 0)
        + int(activity_points_delta)
    )

    if new_total < 0:
        raise ValueError("Resulting total points cannot be negative")

    student.total_points_earned = new_total

    item = StudentPointAdjustment(
        student_id=student.id,
        activity_type_id=int(activity_type_id),

        # Keep the admin-configured amount here.
        # Equivalent hours are reflected in StudentActivityStats.
        delta_points=int(points),

        new_total_points=new_total,
        reason=remarks,
        created_by_admin_id=created_by_admin_id,
        activity_name=activity_name.strip(),
        category=category.strip() if category else None,
        activity_date=date,
        status=status,
        remarks=remarks.strip() if remarks else None,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)

    return item, new_total


async def update_student_point_adjustment(
    db: AsyncSession,
    *,
    adjustment_id: int,
    activity_type_id: int | None,
    activity_name: str | None,
    category: str | None,
    points: int | None,
    date,
    status: str | None,
    remarks: str | None,
) -> tuple[StudentPointAdjustment, int]:
    adj_res = await db.execute(
        select(StudentPointAdjustment)
        .where(StudentPointAdjustment.id == adjustment_id)
        .with_for_update()
    )
    adj = adj_res.scalar_one_or_none()
    if not adj:
        raise ValueError("Activity point entry not found")

    student_res = await db.execute(
        select(Student)
        .where(Student.id == adj.student_id)
        .with_for_update()
    )
    student = student_res.scalar_one_or_none()
    if not student:
        raise ValueError("Student not found")

    old_activity_type_id = getattr(
        adj,
        "activity_type_id",
        None,
    )

    old_points = int(
        adj.delta_points or 0
    )

    old_status = str(
        adj.status or "approved"
    ).lower()

    new_activity_type_id = (
        int(activity_type_id)
        if activity_type_id is not None
        else old_activity_type_id
    )

    if new_activity_type_id is None:
        raise ValueError("Activity type is required")

    new_points = (
        int(points)
        if points is not None
        else old_points
    )

    new_status = (
        str(status).lower()
        if status is not None
        else old_status
    )

    # Validate destination activity type.
    activity_type = await db.get(
        ActivityType,
        int(new_activity_type_id),
    )

    if not activity_type:
        raise ValueError("Activity type not found")

    if not getattr(activity_type, "is_active", True):
        raise ValueError("Activity type is not active")

    total_activity_points_delta = 0

    # Remove the OLD approved contribution.
    if (
        old_activity_type_id is not None
        and old_status == "approved"
        and old_points != 0
    ):
        removed_delta = await _apply_manual_activity_points_delta(
            db,
            student_id=student.id,
            activity_type_id=int(old_activity_type_id),
            delta_manual_points=-old_points,
        )

        total_activity_points_delta += int(
            removed_delta
        )

    # Apply the NEW approved contribution.
    if (
        new_status == "approved"
        and new_points != 0
    ):
        added_delta = await _apply_manual_activity_points_delta(
            db,
            student_id=student.id,
            activity_type_id=int(new_activity_type_id),
            delta_manual_points=new_points,
        )

        total_activity_points_delta += int(
            added_delta
        )

    new_total = (
        int(student.total_points_earned or 0)
        + total_activity_points_delta
    )

    if new_total < 0:
        raise ValueError(
            "Resulting total points cannot be negative"
        )

    student.total_points_earned = new_total

    adj.activity_type_id = int(
        new_activity_type_id
    )

    if activity_name is not None:
        adj.activity_name = activity_name.strip()

    if category is not None:
        adj.category = category.strip() or None

    if status is not None:
        adj.status = status

    if remarks is not None:
        adj.remarks = remarks.strip() or None
        adj.reason = remarks.strip() or None

    if points is not None:
        adj.delta_points = new_points

    if date is not None:
        adj.activity_date = date

    adj.new_total_points = new_total

    await db.flush()
    await db.refresh(adj)

    return adj, new_total


async def delete_student_point_adjustment(
    db: AsyncSession,
    *,
    adjustment_id: int,
) -> int:
    adj_res = await db.execute(
        select(StudentPointAdjustment)
        .where(StudentPointAdjustment.id == adjustment_id)
        .with_for_update()
    )
    adj = adj_res.scalar_one_or_none()

    if not adj:
        raise ValueError("Activity point entry not found")

    student_res = await db.execute(
        select(Student)
        .where(Student.id == adj.student_id)
        .with_for_update()
    )
    student = student_res.scalar_one_or_none()

    if not student:
        raise ValueError("Student not found")

    activity_type_id = getattr(
        adj,
        "activity_type_id",
        None,
    )

    stored_points = int(
        adj.delta_points or 0
    )

    status = str(
        adj.status or "approved"
    ).lower()

    total_points_delta = 0

    # New linked Admin Set Points entries:
    # remove their equivalent-hour contribution.
    if (
        activity_type_id is not None
        and status == "approved"
        and stored_points != 0
    ):
        total_points_delta = await _apply_manual_activity_points_delta(
            db,
            student_id=student.id,
            activity_type_id=int(activity_type_id),
            delta_manual_points=-stored_points,
        )

        new_total = (
            int(student.total_points_earned or 0)
            + int(total_points_delta)
        )

    # Safety fallback for any old unlinked adjustment.
    elif activity_type_id is None and status == "approved":
        new_total = (
            int(student.total_points_earned or 0)
            - stored_points
        )

    else:
        new_total = int(
            student.total_points_earned or 0
        )

    if new_total < 0:
        new_total = 0

    student.total_points_earned = new_total

    await db.delete(adj)
    await db.flush()

    return new_total
