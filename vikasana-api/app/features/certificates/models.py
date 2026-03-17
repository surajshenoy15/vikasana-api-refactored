from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class CertificateCounter(Base):
    __tablename__ = "certificate_counters"
    __table_args__ = (UniqueConstraint("month_code", "academic_year", name="uq_cert_counter_month_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month_code: Mapped[str] = mapped_column(String(8), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(16), nullable=False)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("submission_id", "activity_type_id", name="uq_cert_submission_activity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    certificate_no: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("event_submissions.id"),
        nullable=False,
        index=True,
    )

    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"),
        nullable=False,
        index=True,
    )

    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id"),
        nullable=False,
        index=True,
    )

    activity_type_id: Mapped[int] = mapped_column(
        ForeignKey("activity_types.id"),
        nullable=False,
        index=True,
    )

    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    student = relationship("Student")
    event = relationship("Event")
    submission = relationship("EventSubmission")