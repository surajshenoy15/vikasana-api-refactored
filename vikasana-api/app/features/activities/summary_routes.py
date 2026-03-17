from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_student

from app.features.activities.schemas.activity_summary import StudentActivitySummaryOut
from app.features.activities.summary_service import get_student_activity_summary

router = APIRouter(prefix="/student/activity", tags=["Student - Activity"])


@router.get("/summary", response_model=StudentActivitySummaryOut)
async def summary(
    db: AsyncSession = Depends(get_db),
    student=Depends(get_current_student),
):
    return await get_student_activity_summary(db, student.id)