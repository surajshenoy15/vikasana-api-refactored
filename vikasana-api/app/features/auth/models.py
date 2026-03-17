from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Admin(Base):
    """
    Dedicated `admins` table — completely separate from your existing tables.
    Your users/activity_sessions/certificates tables are NOT touched at all.

    Columns:
      id             INT  — auto-increment primary key
      name           TEXT — display name shown in the UI header
      email          TEXT — unique login email
      password_hash  TEXT — bcrypt hash (plaintext never stored)
      is_active      BOOL — False = account disabled, cannot login
      last_login_at  TS   — updated on every successful login
      created_at     TS   — when the admin row was created
    """
    __tablename__ = "admins"

    id:            Mapped[int]             = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    name:          Mapped[str]             = mapped_column(String(255), nullable=False)
    email:         Mapped[str]             = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str]             = mapped_column(Text, nullable=False)
    is_active:     Mapped[bool]            = mapped_column(Boolean, default=True, nullable=False, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:    Mapped[datetime]        = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Admin id={self.id} email={self.email!r} active={self.is_active}>"

from sqlalchemy import String, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StudentOtpSession(Base):
    __tablename__ = "student_otp_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    otp_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    otp_expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    used_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )