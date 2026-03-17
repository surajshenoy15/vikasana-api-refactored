from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.activities.models import ActivityFaceCheck
from app.features.activities.models import ActivityPhoto
from app.features.activities.models import ActivitySession


async def upsert_face_check(
    db: AsyncSession,
    *,
    session_id: int,
    photo_id: int,
    matched: bool,
    cosine_score: Optional[float] = None,
    l2_score: Optional[float] = None,
    total_faces: Optional[int] = None,
    processed_object: Optional[str] = None,
    reason: Optional[str] = None,
) -> ActivityFaceCheck:
    """
    Creates a new ActivityFaceCheck for (session_id, photo_id)
    OR updates the existing one (unique constraint safe).

    Critical: student_id is ALWAYS derived from ActivityPhoto.student_id.
    """

    # --- Load Photo (source of truth for student_id)
    photo = await db.get(ActivityPhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="ActivityPhoto not found")

    if photo.student_id is None:
        raise HTTPException(
            status_code=400,
            detail="ActivityPhoto.student_id is NULL; cannot create face check",
        )

    # --- Load Session (optional but recommended validation)
    session = await db.get(ActivitySession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="ActivitySession not found")

    # Ensure photo belongs to that session
    if photo.session_id != session_id:
        raise HTTPException(status_code=400, detail="photo_id does not belong to session_id")

    # Ensure same student
    if session.student_id != photo.student_id:
        raise HTTPException(status_code=400, detail="session and photo belong to different students")

    student_id = photo.student_id

    # --- Check existing row (unique constraint: session_id + photo_id)
    existing = await db.execute(
        select(ActivityFaceCheck).where(
            ActivityFaceCheck.session_id == session_id,
            ActivityFaceCheck.photo_id == photo_id,
        )
    )
    face_check = existing.scalar_one_or_none()

    if face_check:
        # UPDATE existing
        face_check.student_id = student_id
        face_check.matched = matched
        face_check.cosine_score = cosine_score
        face_check.l2_score = l2_score
        face_check.total_faces = total_faces
        face_check.processed_object = processed_object
        face_check.reason = reason

        await db.commit()
        await db.refresh(face_check)
        return face_check

    # CREATE new
    face_check = ActivityFaceCheck(
        student_id=student_id,
        session_id=session_id,
        photo_id=photo_id,
        matched=matched,
        cosine_score=cosine_score,
        l2_score=l2_score,
        total_faces=total_faces,
        processed_object=processed_object,
        reason=reason,
    )
    db.add(face_check)

    await db.commit()
    await db.refresh(face_check)
    return face_check