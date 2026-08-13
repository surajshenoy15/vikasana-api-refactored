import os
import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.features.faculty.models import Faculty
from app.features.faculty.models import FacultyActivationSession
from app.features.faculty.schemas.faculty import FacultyCreateRequest, FacultyUpdateRequest
from app.features.organization.models import (
    College,
    CollegeAlias,
    Department,
    FacultyAccessHistory,
)
from app.core.file_storage import upload_faculty_image
from app.core.email_service import send_activation_email, send_faculty_otp_email
from app.core.faculty_tokens import (
    create_activation_token,
    hash_token,
    verify_token,
    activation_expiry_dt,
)

# ✅ Use ONLY passlib via security.py — never import raw bcrypt directly.
# This ensures hash_password() and verify_password() always use the same
# bcrypt implementation and format, preventing the passlib checksum error.
from app.core.security import hash_password


# ---------------------------
# Helpers
# ---------------------------

def mask_email(email: str) -> str:
    """
    a*****z@domain.com
    """
    try:
        name, domain = email.split("@", 1)
    except ValueError:
        return email
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + ("*" * (len(name) - 2)) + name[-1]
    return masked + "@" + domain


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def generate_otp() -> str:
    # 6-digit OTP
    import random
    return f"{random.randint(0, 999999):06d}"


def hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# --------------------------------------------------
# FACULTY ACCESS / ROLE AUDIT
# --------------------------------------------------


async def add_faculty_access_history(
    db: AsyncSession,
    *,
    faculty_id: int,
    action: str,
    college: str,
    previous_role: str | None = None,
    new_role: str | None = None,
    previous_parent_faculty_id: int | None = None,
    new_parent_faculty_id: int | None = None,
    department_id: int | None = None,
    changed_by_admin_id: int | None = None,
    changed_by_faculty_id: int | None = None,
    note: str | None = None,
) -> FacultyAccessHistory:
    """
    Add one immutable Faculty hierarchy/access audit entry.

    This helper deliberately DOES NOT commit.
    The caller owns the surrounding transaction.

    Exactly one actor must be supplied:
        changed_by_admin_id
        OR
        changed_by_faculty_id
    """

    admin_actor = changed_by_admin_id is not None
    faculty_actor = changed_by_faculty_id is not None

    if admin_actor == faculty_actor:
        raise HTTPException(
            status_code=400,
            detail=(
                "Exactly one access-history actor is required: "
                "Admin or Faculty"
            ),
        )

    normalized_action = (action or "").strip()

    if not normalized_action:
        raise HTTPException(
            status_code=400,
            detail="Access-history action is required",
        )

    normalized_college = (college or "").strip()

    if not normalized_college:
        raise HTTPException(
            status_code=400,
            detail="Access-history college is required",
        )

    history = FacultyAccessHistory(
        faculty_id=faculty_id,
        action=normalized_action,
        previous_role=previous_role,
        new_role=new_role,
        previous_parent_faculty_id=previous_parent_faculty_id,
        new_parent_faculty_id=new_parent_faculty_id,
        college=normalized_college,
        department_id=department_id,
        changed_by_admin_id=changed_by_admin_id,
        changed_by_faculty_id=changed_by_faculty_id,
        note=note.strip() if note and note.strip() else None,
    )

    db.add(history)

    # Allocate/validate the row inside the current transaction.
    # No commit happens here.
    await db.flush()

    return history


# ---------------------------
# Existing: Create Faculty + Send Activation Email (unchanged behavior)
# ---------------------------

async def prepare_faculty_creation(
    payload: FacultyCreateRequest,
    db: AsyncSession,
    image_bytes: bytes | None = None,
    image_content_type: str | None = None,
    image_filename: str | None = None,
    *,
    parent_faculty_id: int | None = None,
    created_by_admin_id: int | None = None,
    created_by_faculty_id: int | None = None,
) -> tuple[Faculty, str]:
    """
    Prepare a new Faculty account inside the caller's transaction.

    This function deliberately DOES NOT commit and DOES NOT send the
    activation email.

    It is used by hierarchy flows that must atomically create:
        Faculty + faculty_access_history

    The returned plaintext activation token must only be used for
    sending the activation email after the transaction commits.
    """

    normalized_email = str(payload.email).strip().lower()

    q = await db.execute(
        select(Faculty).where(
            func.lower(func.trim(Faculty.email))
            == normalized_email
        )
    )

    existing = q.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Faculty already exists with this email",
        )

    # ---------------------------------------------------------
    # College Coordinator uniqueness guard.
    #
    # Only one Faculty record may currently hold the
    # college_coordinator role for a canonical college.
    #
    # Canonical college name + active aliases are treated as
    # the same college. Historical Faculty.college values are
    # never rewritten or deleted.
    # ---------------------------------------------------------
    if payload.role == "college_coordinator":
        normalized_college = payload.college.strip().lower()

        canonical_result = await db.execute(
            select(College).where(
                func.lower(func.trim(College.name))
                == normalized_college
            )
        )

        canonical_college = canonical_result.scalar_one_or_none()

        if canonical_college is None:
            alias_college_result = await db.execute(
                select(College)
                .join(
                    CollegeAlias,
                    CollegeAlias.college_id == College.id,
                )
                .where(
                    CollegeAlias.is_active.is_(True),
                    func.lower(func.trim(CollegeAlias.alias))
                    == normalized_college,
                )
            )

            canonical_college = (
                alias_college_result.scalars().first()
            )

        if canonical_college is not None:
            alias_result = await db.execute(
                select(CollegeAlias.alias).where(
                    CollegeAlias.college_id == canonical_college.id,
                    CollegeAlias.is_active.is_(True),
                )
            )

            accepted_names = [
                canonical_college.name,
                *alias_result.scalars().all(),
            ]

            normalized_names = [
                name.strip().lower()
                for name in accepted_names
                if name and name.strip()
            ]
        else:
            normalized_names = [
                normalized_college
            ]

        coordinator_result = await db.execute(
            select(Faculty).where(
                func.lower(func.trim(Faculty.college)).in_(
                    normalized_names
                ),
                func.lower(func.trim(Faculty.role))
                == "college_coordinator",
            )
        )

        existing_coordinator = (
            coordinator_result.scalars().first()
        )

        if existing_coordinator is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A College Coordinator is already assigned "
                    "to this college"
                ),
            )

    if payload.department_id is not None:
        normalized_college = payload.college.strip().lower()

        department_result = await db.execute(
            select(Department).where(
                Department.id == payload.department_id,
                Department.is_active.is_(True),
                func.lower(func.trim(Department.college))
                == normalized_college,
            )
        )

        department = department_result.scalar_one_or_none()

        if not department:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Department must be active and belong to the same "
                    "college as the faculty"
                ),
            )

    image_url = None

    if image_bytes and image_filename:
        image_url = await upload_faculty_image(
            file_bytes=image_bytes,
            content_type=image_content_type or "image/jpeg",
            filename=image_filename,
        )

    token = create_activation_token(normalized_email)
    token_hash = hash_token(token)

    faculty = Faculty(
        full_name=payload.full_name.strip(),
        college=payload.college.strip(),
        email=normalized_email,
        role=payload.role,
        department_id=payload.department_id,
        parent_faculty_id=parent_faculty_id,
        created_by_admin_id=created_by_admin_id,
        created_by_faculty_id=created_by_faculty_id,
        is_active=False,
        activation_token_hash=token_hash,
        activation_expires_at=activation_expiry_dt(),
        image_url=image_url,
    )

    db.add(faculty)

    # Allocate the Faculty ID without committing the transaction.
    await db.flush()
    await db.refresh(faculty)

    return faculty, token


async def send_faculty_activation_email(
    faculty: Faculty,
    token: str,
) -> bool:
    """
    Send the activation email after the account transaction succeeds.

    Email failure does not roll back an already committed Faculty
    account; the caller receives False and can surface that state.
    """

    frontend_base = os.getenv(
        "FRONTEND_BASE_URL",
        "",
    ).rstrip("/")

    if frontend_base:
        activate_url = (
            f"{frontend_base}/activate?token={token}"
        )
    else:
        activate_url = (
            "http://31.97.230.171:8000"
            "/api/faculty/activate?token="
            + token
        )

    try:
        await send_activation_email(
            to_email=faculty.email,
            to_name=faculty.full_name,
            activate_url=activate_url,
            role=faculty.role,
        )
        return True

    except Exception as error:
        print(
            "[WARN] Activation email not sent for "
            f"{faculty.email}: {error}"
        )
        return False


async def resend_faculty_activation(
    faculty_id: int,
    db: AsyncSession,
) -> tuple[Faculty, bool]:
    """
    Regenerate an activation invitation for an existing pending account.

    Safety:
    - Never overwrites an existing password.
    - Invalidates all previous OTP activation sessions.
    - Preserves hierarchy, role, provenance, and historical data.
    """

    result = await db.execute(
        select(Faculty).where(
            Faculty.id == faculty_id
        )
    )
    faculty = result.scalar_one_or_none()

    if not faculty:
        raise HTTPException(
            status_code=404,
            detail="Faculty account not found",
        )

    # Accounts that already have a password are not pending invitations.
    # This also prevents a soft-deactivated account from being reactivated
    # through the invitation flow.
    if faculty.password_hash:
        raise HTTPException(
            status_code=400,
            detail=(
                "Account already has a password. "
                "Activation invitation cannot be resent."
            ),
        )

    # Invalidate every older OTP/session for this account before issuing
    # a new invitation.
    session_result = await db.execute(
        select(FacultyActivationSession).where(
            FacultyActivationSession.faculty_id == faculty.id
        )
    )

    for session in session_result.scalars().all():
        await db.delete(session)

    token = create_activation_token(faculty.email)

    faculty.activation_token_hash = hash_token(token)
    faculty.activation_expires_at = activation_expiry_dt()

    try:
        await db.commit()
        await db.refresh(faculty)
    except Exception:
        await db.rollback()
        raise

    activation_email_sent = await send_faculty_activation_email(
        faculty,
        token,
    )

    return faculty, activation_email_sent


async def create_faculty(
    payload: FacultyCreateRequest,
    db: AsyncSession,
    image_bytes: bytes | None = None,
    image_content_type: str | None = None,
    image_filename: str | None = None,
    *,
    parent_faculty_id: int | None = None,
    created_by_admin_id: int | None = None,
    created_by_faculty_id: int | None = None,
) -> tuple[Faculty, bool]:
    """
    Backward-compatible Faculty creation wrapper.

    Existing Admin callers continue to receive:
        (faculty, activation_email_sent)

    Transaction:
        prepare Faculty
        -> commit
        -> send activation email
    """

    try:
        faculty, token = await prepare_faculty_creation(
            payload=payload,
            db=db,
            image_bytes=image_bytes,
            image_content_type=image_content_type,
            image_filename=image_filename,
            parent_faculty_id=parent_faculty_id,
            created_by_admin_id=created_by_admin_id,
            created_by_faculty_id=created_by_faculty_id,
        )

        await add_faculty_access_history(
            db=db,
            faculty_id=faculty.id,
            action="created",
            college=faculty.college,
            previous_role=None,
            new_role=faculty.role,
            previous_parent_faculty_id=None,
            new_parent_faculty_id=faculty.parent_faculty_id,
            department_id=faculty.department_id,
            changed_by_admin_id=created_by_admin_id,
            changed_by_faculty_id=created_by_faculty_id,
            note="Faculty account created",
        )

        await db.commit()
        await db.refresh(faculty)

    except Exception:
        await db.rollback()
        raise

    email_sent = await send_faculty_activation_email(
        faculty,
        token,
    )

    return faculty, email_sent


async def update_faculty(
    faculty_id: int,
    payload: FacultyUpdateRequest,
    db: AsyncSession,
) -> Faculty:
    faculty_result = await db.execute(
        select(Faculty).where(Faculty.id == faculty_id)
    )
    faculty = faculty_result.scalar_one_or_none()

    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    fields_set = payload.model_fields_set

    if not fields_set:
        raise HTTPException(
            status_code=400,
            detail="No faculty fields were provided for update",
        )

    effective_college = faculty.college

    if "college" in fields_set:
        if payload.college is None:
            raise HTTPException(
                status_code=400,
                detail="college cannot be null",
            )

        effective_college = payload.college.strip()

        if not effective_college:
            raise HTTPException(
                status_code=400,
                detail="college cannot be empty",
            )

    normalized_email = None

    if "email" in fields_set:
        if payload.email is None:
            raise HTTPException(
                status_code=400,
                detail="email cannot be null",
            )

        normalized_email = str(payload.email).strip().lower()

        duplicate_result = await db.execute(
            select(Faculty).where(
                func.lower(func.trim(Faculty.email)) == normalized_email,
                Faculty.id != faculty_id,
            )
        )

        if duplicate_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="Faculty already exists with this email",
            )

    department_was_provided = "department_id" in fields_set
    college_was_provided = "college" in fields_set

    effective_department_id = (
        payload.department_id
        if department_was_provided
        else faculty.department_id
    )

    if (
        effective_department_id is not None
        and (department_was_provided or college_was_provided)
    ):
        department_result = await db.execute(
            select(Department).where(
                Department.id == effective_department_id,
                Department.is_active.is_(True),
                func.lower(func.trim(Department.college))
                == effective_college.lower(),
            )
        )

        if not department_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Department must be active and belong to the same "
                    "college as the faculty"
                ),
            )

    if "full_name" in fields_set:
        if payload.full_name is None or not payload.full_name.strip():
            raise HTTPException(
                status_code=400,
                detail="full_name cannot be null or empty",
            )

        faculty.full_name = payload.full_name.strip()

    if college_was_provided:
        faculty.college = effective_college

    if "email" in fields_set:
        faculty.email = normalized_email

    if "role" in fields_set:
        if payload.role is None or not payload.role.strip():
            raise HTTPException(
                status_code=400,
                detail="role cannot be null or empty",
            )

        faculty.role = payload.role.strip()

    if department_was_provided:
        faculty.department_id = payload.department_id

    # legacy_college_scope is deliberately preserved.
    try:
        await db.commit()
        await db.refresh(faculty)
    except Exception:
        await db.rollback()
        raise

    return faculty


# ==========================================================
# NEW FLOW:
# 1) validate token -> create activation session
# 2) send OTP
# 3) verify OTP -> return set_password_token
# 4) set password -> activate account
# ==========================================================

async def validate_activation_token_and_create_session(
    token: str,
    db: AsyncSession,
) -> tuple[str, str, datetime]:
    """
    Validates activation link token. Creates activation session.
    Returns: (activation_session_id, masked_email, activation_link_expires_at)
    """
    max_age_seconds = int(os.getenv("ACTIVATION_TOKEN_EXPIRE_HOURS", "48")) * 3600

    try:
        data = verify_token(token, max_age_seconds=max_age_seconds)
        email = data.get("email")
        if not email:
            raise ValueError("Invalid token payload")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired activation token")

    q = await db.execute(select(Faculty).where(Faculty.email == email))
    faculty = q.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    # Already fully activated (active + password set) => stop
    if faculty.is_active and getattr(faculty, "password_hash", None):
        raise HTTPException(status_code=400, detail="Account already activated")

    # Verify token hash stored in DB
    if not faculty.activation_token_hash or faculty.activation_token_hash != hash_token(token):
        raise HTTPException(status_code=400, detail="Invalid activation token")

    if faculty.activation_expires_at and faculty.activation_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Activation link expired")

    # Create activation session
    session_id = generate_session_id()
    sess = FacultyActivationSession(
        id=session_id,
        faculty_id=faculty.id,
        otp_hash=None,
        otp_expires_at=None,
        otp_attempts=0,
        otp_verified_at=None,
    )
    db.add(sess)
    await db.commit()

    return session_id, mask_email(faculty.email), faculty.activation_expires_at


async def send_activation_otp(
    activation_session_id: str,
    db: AsyncSession,
) -> None:
    """
    Generates OTP, stores hash+expiry in activation session and sends OTP email.
    """
    q = await db.execute(
        select(FacultyActivationSession).where(FacultyActivationSession.id == activation_session_id)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activation session not found")

    fq = await db.execute(select(Faculty).where(Faculty.id == sess.faculty_id))
    faculty = fq.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    otp = generate_otp()

    print("\n========= FACULTY OTP =========")
    print(f"EMAIL: {faculty.email}")
    print(f"OTP  : {otp}")
    print("================================\n")

    sess.otp_hash = hash_otp(otp)
    sess.otp_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    await db.commit()

    await send_faculty_otp_email(
        to_email=faculty.email,
        to_name=faculty.full_name,
        otp=otp,
        role=faculty.role,
    )


async def verify_activation_otp(
    activation_session_id: str,
    otp: str,
    db: AsyncSession,
) -> str:
    """
    Verifies OTP. On success returns a short-lived set_password_token.
    """
    q = await db.execute(
        select(FacultyActivationSession).where(FacultyActivationSession.id == activation_session_id)
    )
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activation session not found")

    if not sess.otp_hash or not sess.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP not generated yet. Please send OTP first.")

    if sess.otp_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="OTP expired. Please resend OTP.")

    if sess.otp_attempts >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts. Please resend OTP.")

    ok = constant_time_equals(sess.otp_hash, hash_otp(otp))
    sess.otp_attempts += 1

    if not ok:
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid OTP")

    sess.otp_verified_at = datetime.now(timezone.utc)
    await db.commit()

    s_secret = os.getenv("ACTIVATION_TOKEN_SECRET", "change-me")
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(secret_key=s_secret, salt="faculty-activation")

    set_password_token = s.dumps({"session_id": activation_session_id, "purpose": "set-password"})
    return set_password_token


async def set_password_after_otp(
    set_password_token: str,
    new_password: str,
    db: AsyncSession,
) -> None:
    """
    Sets password, activates account, clears activation token, deletes session.

    ✅ Uses hash_password() from security.py (passlib/bcrypt) — the same
       function used everywhere else — so verify_password() will always work.
    """
    # Validate token (15 min)
    s_secret = os.getenv("ACTIVATION_TOKEN_SECRET", "change-me")
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(secret_key=s_secret, salt="faculty-activation")

    try:
        data = s.loads(set_password_token, max_age=15 * 60)
        if data.get("purpose") != "set-password":
            raise ValueError("Wrong token purpose")
        session_id = data.get("session_id")
        if not session_id:
            raise ValueError("Missing session_id")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired set password token")

    q = await db.execute(select(FacultyActivationSession).where(FacultyActivationSession.id == session_id))
    sess = q.scalar_one_or_none()
    if not sess:
        raise HTTPException(status_code=404, detail="Activation session not found")

    if not sess.otp_verified_at:
        raise HTTPException(status_code=400, detail="OTP not verified")

    fq = await db.execute(select(Faculty).where(Faculty.id == sess.faculty_id))
    faculty = fq.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    # ✅ hash_password() imported from security.py — uses passlib, not raw bcrypt
    faculty.password_hash     = hash_password(new_password)
    faculty.password_set_at   = datetime.now(timezone.utc)
    faculty.is_active         = True

    # Clear activation token fields (single-use link)
    faculty.activation_token_hash = None
    faculty.activation_expires_at = None

    # Cleanup session
    await db.delete(sess)
    await db.commit()


# ----------------------------------------------------------
# OPTIONAL: Keep old activate_faculty() for backward compatibility
# ----------------------------------------------------------

async def activate_faculty(token: str, db: AsyncSession) -> None:
    """
    OLD behavior: activates immediately without OTP.
    Keep only if you want backward compatibility; otherwise remove route.
    """
    max_age_seconds = int(os.getenv("ACTIVATION_TOKEN_EXPIRE_HOURS", "48")) * 3600
    try:
        data = verify_token(token, max_age_seconds=max_age_seconds)
        email = data.get("email")
        if not email:
            raise ValueError("Invalid token payload")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired activation token")

    q = await db.execute(select(Faculty).where(Faculty.email == email))
    faculty = q.scalar_one_or_none()
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty not found")

    if faculty.is_active:
        return

    if not faculty.activation_token_hash or faculty.activation_token_hash != hash_token(token):
        raise HTTPException(status_code=400, detail="Invalid activation token")

    if faculty.activation_expires_at and faculty.activation_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Activation link expired")

    faculty.is_active = True
    faculty.activation_token_hash = None
    faculty.activation_expires_at = None

    await db.commit()