from __future__ import annotations

from datetime import datetime
from typing import List, TYPE_CHECKING

from sqlalchemy import String, Boolean, DateTime, Text, Index, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.features.students.models import Student


class Faculty(Base):
    __tablename__ = "faculty"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    college: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    role: Mapped[str] = mapped_column(String(50), nullable=False, default="faculty")

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    activation_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    students_created: Mapped[List["Student"]] = relationship(
        "Student",
        back_populates="created_by_faculty",
        cascade="all",
    )

    __table_args__ = (
        Index("ix_faculty_email", "email"),
    )


class FacultyActivationSession(Base):
    __tablename__ = "faculty_activation_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    faculty_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("faculty.id"),
        index=True,
        nullable=False
    )

    otp_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    otp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    otp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow
    )