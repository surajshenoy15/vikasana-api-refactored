from datetime import datetime

from pydantic import BaseModel, Field


class WebsiteFacultyDashboardStatsOut(BaseModel):
    """
    Role-scoped summary for the website Faculty dashboard.

    This response is separate from the existing mobile Faculty dashboard
    and the existing global Admin dashboard.
    """

    totalStudents: int = Field(ge=0)
    activeStudents: int = Field(ge=0)

    totalFaculty: int = Field(ge=0)
    pendingFaculty: int = Field(ge=0)

    totalActivities: int = Field(ge=0)
    approvedActivities: int = Field(ge=0)

    totalCertificates: int = Field(ge=0)

    # Authenticated website Faculty identity / management scope.
    facultyName: str | None = None
    departmentId: int | None = Field(default=None, ge=1)
    departmentName: str | None = None
    departmentCode: str | None = None

    asOf: datetime | None = None
