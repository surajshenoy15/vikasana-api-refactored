from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CollegeOrganizationSetting(Base):
    __tablename__ = "college_organization_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    college: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    department_architecture_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    academic_year_start_month: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=7,
        server_default=text("7"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "academic_year_start_month BETWEEN 1 AND 12",
            name="ck_college_org_academic_year_start_month",
        ),
        Index(
            "ix_college_organization_settings_college",
            "college",
        ),
    )


Index(
    "uq_college_organization_settings_college_ci",
    func.lower(func.trim(CollegeOrganizationSetting.college)),
    unique=True,
)