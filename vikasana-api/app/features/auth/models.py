from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Admin(Base):
    """
    Dedicated admins table.
    """
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # Platform role:
    # - admin
    # - super_admin
    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="admin",
        server_default="admin",
    )

    # Incrementing this value invalidates all previously issued
    # admin access tokens once token-version enforcement is enabled.
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        server_default="true",
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Admin id={self.id} email={self.email!r} active={self.is_active}>"


class AdminMFAOtp(Base):
    """
    Admin MFA OTP sessions.

    Flow:
    1. Admin enters email + password.
    2. Backend creates one AdminMFAOtp row.
    3. OTP is sent to admin email.
    4. Admin verifies OTP using mfa_token + otp.
    5. Backend marks this row used and returns final access token.
    """
    __tablename__ = "admin_mfa_otps"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    admin_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("admins.id"),
        nullable=False,
        index=True,
    )

    otp_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    mfa_token: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class StudentOtpSession(Base):
    __tablename__ = "student_otp_sessions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    otp_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    otp_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AdminSession(Base):
    """
    Login/device session for an Admin or Super Admin.

    This table will later support:
    - Device list
    - Last active information
    - Logout one device
    - Logout all devices
    - Remote session revocation

    Session enforcement is implemented separately in the
    authentication dependency/service.
    """

    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    session_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )

    admin_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "admins.id",
            ondelete="CASCADE",
        ),
        index=True,
        nullable=False,
    )

    # =====================================================
    # DEVICE
    # =====================================================

    device_id: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
        nullable=True,
    )

    device_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    device_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    device_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    platform: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # =====================================================
    # OS / BROWSER
    # =====================================================

    os_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    os_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    browser_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    browser_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # =====================================================
    # APP
    # =====================================================

    app_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    app_version: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # =====================================================
    # NETWORK
    # =====================================================

    ip_address: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    country: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    region: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    city: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =====================================================
    # SESSION STATE
    # =====================================================

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_by_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    revoke_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AdminSession id={self.id} "
            f"admin_id={self.admin_id} "
            f"session_id={self.session_id!r} "
            f"status={self.status!r}>"
        )
