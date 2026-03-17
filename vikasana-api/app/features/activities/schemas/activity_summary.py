from pydantic import BaseModel
from typing import List, Optional

from app.features.students.models import StudentType


class ActivityTypeProgress(BaseModel):
    activity_type_id: int
    activity_type_name: str
    total_hours: float
    points_awarded: int
    max_points: int
    completed: bool


class StudentActivitySummaryOut(BaseModel):
    student_id: int
    student_type: StudentType

    required_points: int
    earned_points: int
    remaining_points: int
    completion_percent: float
    is_completed: bool

    breakdown: List[ActivityTypeProgress]