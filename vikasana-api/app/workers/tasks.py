# app/workers/tasks.py — Background task definitions
"""
Heavy tasks offloaded from request cycle:
- Certificate PDF generation
- Email sending
- Activity image processing

Usage from any service:
    from app.workers.tasks import generate_certificate_pdf
    generate_certificate_pdf.delay(cert_id=123, student_id=456, event_id=789)
"""
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
def generate_certificate_pdf(self, *, cert_id: int, student_id: int, event_id: int, **kwargs):
    """
    Generate certificate PDF in background.
    Called after submission approval to avoid blocking the HTTP response.
    """
    try:
        # Import here to avoid circular imports at module level
        from app.core.cert_pdf import build_certificate_pdf
        from app.core.cert_storage import upload_certificate_pdf_bytes
        from app.core.config import settings

        pdf_bytes = build_certificate_pdf(
            template_pdf_path=settings.CERT_TEMPLATE_PDF_PATH,
            certificate_no=kwargs.get("certificate_no", ""),
            issue_date=kwargs.get("issue_date", ""),
            student_name=kwargs.get("student_name", "Student"),
            usn=kwargs.get("usn", ""),
            activity_type=kwargs.get("activity_type", ""),
            venue_name=kwargs.get("venue_name", "N/A"),
            activity_points=kwargs.get("activity_points", 0),
            verify_url=kwargs.get("verify_url", ""),
        )

        object_key = upload_certificate_pdf_bytes(cert_id, pdf_bytes)
        return {"status": "success", "object_key": object_key}

    except Exception as exc:
        self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def send_email_task(self, *, email_type: str, to_email: str, to_name: str, **kwargs):
    """
    Send emails in background to avoid blocking HTTP responses.

    email_type: "activation" | "faculty_otp" | "student_welcome" | "student_otp"
    """
    import asyncio

    try:
        from app.core.email_service import (
            send_activation_email,
            send_faculty_otp_email,
            send_student_welcome_email,
            send_student_otp_email,
        )

        async def _send():
            if email_type == "activation":
                await send_activation_email(to_email, to_name, kwargs["activate_url"])
            elif email_type == "faculty_otp":
                await send_faculty_otp_email(to_email, to_name, kwargs["otp"])
            elif email_type == "student_welcome":
                await send_student_welcome_email(to_email, to_name, kwargs["app_download_url"])
            elif email_type == "student_otp":
                await send_student_otp_email(to_email, to_name, kwargs["otp"])

        asyncio.run(_send())
        return {"status": "sent", "email_type": email_type}

    except Exception as exc:
        self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=5)
def process_activity_image(self, *, student_id: int, session_id: int, photo_id: int, image_url: str):
    """
    Process activity images in background:
    - Face verification
    - Image quality checks
    - Geofence validation
    """
    try:
        # Placeholder for heavy image processing
        return {"status": "processed", "photo_id": photo_id}
    except Exception as exc:
        self.retry(exc=exc)
