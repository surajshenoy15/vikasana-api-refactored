from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession, ActivitySessionStatus
from app.features.activities.models import ActivityType
from app.features.students.models import Student
from app.features.activities.models import StudentActivityProgress
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

    new_total = int(student.total_points_earned or 0) + int(points)
    if new_total < 0:
        raise ValueError("Resulting total points cannot be negative")

    student.total_points_earned = new_total

    item = StudentPointAdjustment(
        student_id=student.id,
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
        select(Student).where(Student.id == adj.student_id).with_for_update()
    )
    student = student_res.scalar_one_or_none()
    if not student:
        raise ValueError("Student not found")

    old_points = int(adj.delta_points or 0)
    new_points = int(points) if points is not None else old_points
    delta_diff = new_points - old_points

    new_total = int(student.total_points_earned or 0) + delta_diff
    if new_total < 0:
        raise ValueError("Resulting total points cannot be negative")

    student.total_points_earned = new_total

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
        select(Student).where(Student.id == adj.student_id).with_for_update()
    )
    student = student_res.scalar_one_or_none()
    if not student:
        raise ValueError("Student not found")

    new_total = int(student.total_points_earned or 0) - int(adj.delta_points or 0)
    if new_total < 0:
        new_total = 0

    student.total_points_earned = new_total

    await db.delete(adj)
    await db.flush()

    return new_total