from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, StringConstraints, EmailStr
from datetime import datetime


NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
USNStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=30)]
BranchStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]

StudentTypeStr = Literal["REGULAR", "DIPLOMA"]


class StudentCreate(BaseModel):
    name: NameStr
    usn: USNStr
    branch: BranchStr

    email: EmailStr | None = None
    student_type: StudentTypeStr = "REGULAR"

    passout_year: int = Field(..., ge=1990, le=2100)
    admitted_year: int = Field(..., ge=1990, le=2100)


class StudentOut(BaseModel):
    id: int
    name: str
    usn: str
    branch: str

    email: str | None
    student_type: str

    passout_year: int
    admitted_year: int

    college: str | None = None
    faculty_mentor_name: str | None = None

    activities_count: int = 0
    certificates_count: int = 0

    # ✅ add this
    total_points_earned: int = 0

    model_config = {"from_attributes": True}


class BulkUploadResult(BaseModel):
    total_rows: int
    inserted: int
    skipped_duplicates: int
    invalid_rows: int
    errors: list[str] = []


# ─────────────────────────────────────────────
# Activity Points Schemas
# ─────────────────────────────────────────────

class StudentPointAdjustmentCreate(BaseModel):
    activity_name: str = Field(..., min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=80)
    points: int
    date: Optional[datetime] = None
    status: Literal["approved", "pending", "rejected"] = "approved"
    remarks: Optional[str] = Field(default=None, max_length=255)


class StudentPointAdjustmentUpdate(BaseModel):
    activity_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    category: Optional[str] = Field(default=None, max_length=80)
    points: Optional[int] = None
    date: Optional[datetime] = None
    status: Optional[Literal["approved", "pending", "rejected"]] = None
    remarks: Optional[str] = Field(default=None, max_length=255)


class StudentPointAdjustmentOut(BaseModel):
    id: int
    activity_name: str
    category: Optional[str] = None
    points: int
    date: Optional[datetime] = None
    status: str
    remarks: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StudentPointAdjustmentListOut(BaseModel):
    total_points: int
    items: list[StudentPointAdjustmentOut]


class StudentPointAdjustmentWriteResponse(BaseModel):
    total_points: int
    item: StudentPointAdjustmentOut