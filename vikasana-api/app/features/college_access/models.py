from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CollegeAccessControl(Base):
    __tablename__ = "college_access_controls"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    college: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_college_access_controls_college", "college"),
    )