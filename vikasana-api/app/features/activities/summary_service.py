from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.features.students.models import Student, StudentType
from app.features.activities.models import ActivityType
from app.features.activities.models import StudentActivityStats


def _required_points(student_type: StudentType) -> int:
    return 60 if student_type == StudentType.DIPLOMA else 100


async def get_student_activity_summary(db: AsyncSession, student_id: int):
    # 1) load student
    res = await db.execute(select(Student).where(Student.id == student_id))
    student = res.scalar_one_or_none()
    if not student:
        raise ValueError("Student not found")

    required = _required_points(student.student_type)

    # 2) get all approved active activity types (to show full breakdown even if student has 0)
    types_res = await db.execute(
        select(ActivityType).where(
            ActivityType.is_active == True,
            ActivityType.status == "APPROVED",  # enum will cast fine
        ).order_by(ActivityType.name.asc())
    )
    activity_types: List[ActivityType] = list(types_res.scalars().all())

    # 3) stats for this student
    stats_res = await db.execute(
        select(StudentActivityStats).where(StudentActivityStats.student_id == student_id)
    )
    stats_list = list(stats_res.scalars().all())
    stats_by_type = {s.activity_type_id: s for s in stats_list}

    breakdown = []
    earned_points = 0

    for t in activity_types:
        s = stats_by_type.get(t.id)
        total_hours = float(s.total_verified_hours) if s else 0.0
        points_awarded = int(s.points_awarded) if s else 0

        earned_points += points_awarded

        breakdown.append(
            {
                "activity_type_id": t.id,
                "activity_type_name": t.name,
                "total_hours": total_hours,
                "points_awarded": points_awarded,
                "max_points": int(t.max_points),
                "completed": points_awarded >= int(t.max_points),
            }
        )

    remaining = max(0, required - earned_points)
    percent = 0.0 if required == 0 else min(100.0, (earned_points / required) * 100.0)

    return {
        "student_id": student.id,
        "student_type": student.student_type,
        "required_points": required,
        "earned_points": earned_points,
        "remaining_points": remaining,
        "completion_percent": round(percent, 2),
        "is_completed": earned_points >= required,
        "breakdown": breakdown,
    }