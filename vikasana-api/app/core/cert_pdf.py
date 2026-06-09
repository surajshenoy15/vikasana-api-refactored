# app/core/cert_pdf.py

import io
import textwrap
from datetime import datetime, timezone
from typing import Optional

import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.colors import black, HexColor

from pypdf import PdfReader, PdfWriter


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def _safe_text(value, fallback: str = "") -> str:
    value = "" if value is None else str(value).strip()
    return value if value else fallback


def _format_issue_date(value) -> str:
    """
    Accepts string/date/datetime and returns dd.mm.yyyy.
    """
    if not value:
        return datetime.now(timezone.utc).strftime("%d.%m.%Y")

    if isinstance(value, str):
        return value

    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")

    # date object
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")

    return str(value)


def _draw_wrapped_text(
    c: canvas.Canvas,
    *,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font_name: str = "Times-Roman",
    font_size: int = 11,
    leading: float = 15,
    indent_first_line: float = 0,
    indent_other_lines: float = 0,
) -> float:
    """
    Draws wrapped paragraph text and returns updated y.
    """
    c.setFont(font_name, font_size)
    c.setFillColor(black)

    words = text.split()
    if not words:
        return y

    line = ""
    current_indent = indent_first_line
    first_line = True

    for word in words:
        test_line = f"{line} {word}".strip()
        allowed_width = max_width - current_indent

        if stringWidth(test_line, font_name, font_size) <= allowed_width:
            line = test_line
        else:
            c.drawString(x + current_indent, y, line)
            y -= leading
            line = word

            first_line = False
            current_indent = indent_other_lines

    if line:
        c.drawString(x + current_indent, y, line)
        y -= leading

    return y


def _draw_wrapped_text_centered(
    c: canvas.Canvas,
    *,
    text: str,
    center_x: float,
    y: float,
    max_width: float,
    font_name: str = "Times-Roman",
    font_size: int = 11,
    leading: float = 15,
) -> float:
    """
    Draws wrapped centered text and returns updated y.
    """
    c.setFont(font_name, font_size)
    c.setFillColor(black)

    words = text.split()
    if not words:
        return y

    line = ""

    for word in words:
        test_line = f"{line} {word}".strip()

        if stringWidth(test_line, font_name, font_size) <= max_width:
            line = test_line
        else:
            c.drawCentredString(center_x, y, line)
            y -= leading
            line = word

    if line:
        c.drawCentredString(center_x, y, line)
        y -= leading

    return y


def _make_qr_image(verify_url: str) -> ImageReader:
    qr = qrcode.QRCode(box_size=3, border=1)
    qr.add_data(verify_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    qr_buf = io.BytesIO()
    img.save(qr_buf, format="PNG")
    qr_buf.seek(0)

    return ImageReader(qr_buf)


# ---------------------------------------------------------
# Certificate body content
# ---------------------------------------------------------

def build_certificate_body(
    *,
    student_name: str,
    usn: str,
    college_name: str,
    event_title: str,
    venue_name: str,
    event_month_year: str,
    academic_year: str,
    activity_type: str,
    activity_points: int,
) -> list[str]:
    """
    Returns paragraph list for the certificate citation/body.
    This follows the flow of the sample certificate:
    participation -> initiative details -> AICTE alignment -> objective
    -> academic year contribution -> points -> appreciation.
    """

    student_name = _safe_text(student_name, "Student")
    usn = _safe_text(usn)
    college_name = _safe_text(college_name, "the institution")
    event_title = _safe_text(event_title, "the activity")
    venue_name = _safe_text(venue_name, "the event venue")
    event_month_year = _safe_text(event_month_year, "the event period")
    academic_year = _safe_text(academic_year, "the academic year")
    activity_type = _safe_text(activity_type, "Social Activity")

    usn_text = f" ({usn})" if usn else ""

    return [
        (
            f"This is to certify that Mr./Ms. {student_name}{usn_text} of "
            f"{college_name} has actively participated in the {event_title} initiative, "
            f"organized at {venue_name} in the month of {event_month_year}. "
            f"This initiative was organized by Vikasana Foundation."
        ),
        (
            "This initiative was designed to encourage student participation in social, "
            "educational, environmental, and community development activities. The activity "
            "falls under the AICTE Activity Point Programme and aligns with the objectives "
            "of experiential learning, social responsibility, community involvement, and "
            "nation-building."
        ),
        (
            f"This activity is recognized under the category of {activity_type}. It aims "
            "to raise awareness about the importance of community participation and "
            "responsible citizenship, highlighting the role of students in contributing "
            "towards meaningful social impact and public welfare."
        ),
        (
            f"During the academic year {academic_year}, the participant demonstrated "
            "dedication, teamwork, discipline, and active involvement, significantly "
            "contributing to the successful completion of the activity."
        ),
        (
            f"In recognition of their efforts, the participant is awarded "
            f"{activity_points} points for their valuable contribution to the community "
            f"and for promoting active participation in socially relevant initiatives."
        ),
        (
            f"We extol Mr./Ms. {student_name}{usn_text} for their enthusiasm, hard work, "
            "and commitment to this noble cause. We look forward to their continued "
            "participation in future initiatives and wish them success in all their "
            "future endeavours."
        ),
    ]


# ---------------------------------------------------------
# Overlay PDF
# ---------------------------------------------------------

def _make_overlay_pdf(
    *,
    certificate_no: str,
    issue_date: str,
    student_name: str,
    usn: str,
    college_name: str,
    event_title: str,
    activity_type: str,
    venue_name: str,
    event_month_year: str,
    academic_year: str,
    activity_points: int,
    verify_url: str,
    signature: Optional[str] = None,
    page_size=A4,
) -> bytes:
    """
    Creates a transparent overlay PDF with certificate text + QR.
    This overlay is merged on top of the blank certificate template.
    """

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    w, h = page_size

    certificate_no = _safe_text(certificate_no)
    issue_date = _format_issue_date(issue_date)

    # -----------------------------------------------------
    # Certificate No + Date
    # -----------------------------------------------------
    top_y = h - 35 * mm

    c.setFillColor(black)
    c.setFont("Times-Roman", 11)

    c.drawString(
        20 * mm,
        top_y,
        f"Certificate No: {certificate_no}"
    )

    c.drawRightString(
        w - 20 * mm,
        top_y,
        f"Date: {issue_date}"
    )

    # -----------------------------------------------------
    # Main body
    # -----------------------------------------------------
    x = 20 * mm
    max_width = w - 40 * mm

    # Starts after template header area
    y = h - 52 * mm

    paragraphs = build_certificate_body(
        student_name=student_name,
        usn=usn,
        college_name=college_name,
        event_title=event_title,
        venue_name=venue_name,
        event_month_year=event_month_year,
        academic_year=academic_year,
        activity_type=activity_type,
        activity_points=activity_points,
    )

    for paragraph in paragraphs:
        y = _draw_wrapped_text(
            c,
            text=paragraph,
            x=x,
            y=y,
            max_width=max_width,
            font_name="Times-Roman",
            font_size=11,
            leading=15,
            indent_first_line=10 * mm,
            indent_other_lines=0,
        )
        y -= 5 * mm

    # -----------------------------------------------------
    # Managing Trustee area
    # -----------------------------------------------------
    trustee_x = w - 45 * mm

    c.setFillColor(black)
    c.setFont("Times-Bold", 11)
    c.drawCentredString(trustee_x, 40 * mm, "Managing Trustee")

    c.setFont("Times-Roman", 9)
    c.drawCentredString(trustee_x, 35 * mm, "Vikasana Foundation")

    # -----------------------------------------------------
    # QR verification
    # -----------------------------------------------------
    if verify_url:
        qr_size = 24 * mm
        qr_x = 20 * mm
        qr_y = 25 * mm

        qr_img = _make_qr_image(verify_url)
        c.drawImage(
            qr_img,
            qr_x,
            qr_y,
            qr_size,
            qr_size,
            mask="auto",
        )

        c.setFont("Times-Roman", 7)
        c.setFillColor(HexColor("#333333"))
        c.drawString(qr_x, qr_y - 4 * mm, "Scan QR to verify")

    # -----------------------------------------------------
    # Small digital signature/hash text
    # -----------------------------------------------------
    if signature:
        c.setFont("Times-Roman", 5.5)
        c.setFillColor(HexColor("#666666"))

        sig_text = f"Digital Verification Signature: {signature}"
        max_sig_chars = 105

        if len(sig_text) > max_sig_chars:
            sig_text = sig_text[:max_sig_chars] + "..."

        c.drawString(20 * mm, 18 * mm, sig_text)

    c.save()
    return buf.getvalue()


# ---------------------------------------------------------
# Public function used by certificate service
# ---------------------------------------------------------

def build_certificate_pdf(
    *,
    template_pdf_path: str,
    certificate_no: str,
    issue_date: str,
    student_name: str,
    usn: str,
    activity_type: str,
    venue_name: str,
    activity_points: int,
    verify_url: str,

    # New DB-driven fields
    college_name: str = "the institution",
    event_title: str = "the activity",
    event_month_year: str = "the event period",
    academic_year: str = "the academic year",

    # Optional existing signature/hash
    signature: Optional[str] = None,
) -> bytes:
    """
    Loads the blank Vikasana certificate template PDF and merges
    a text/QR overlay onto page 1.

    Required dynamic data should be fetched from DB in certificate_service.py:
    - student_name
    - usn
    - college_name
    - event_title
    - venue_name
    - event_month_year
    - academic_year
    - activity_type
    - activity_points
    - certificate_no
    - verify_url
    """

    template_reader = PdfReader(template_pdf_path)

    if not template_reader.pages:
        raise ValueError("Certificate template PDF has no pages.")

    template_page = template_reader.pages[0]

    # Use template page size so overlay matches exactly
    w = float(template_page.mediabox.width)
    h = float(template_page.mediabox.height)

    overlay_bytes = _make_overlay_pdf(
        certificate_no=certificate_no,
        issue_date=issue_date,
        student_name=student_name,
        usn=usn,
        college_name=college_name,
        event_title=event_title,
        activity_type=activity_type,
        venue_name=venue_name,
        event_month_year=event_month_year,
        academic_year=academic_year,
        activity_points=activity_points,
        verify_url=verify_url,
        signature=signature,
        page_size=(w, h),
    )

    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
    overlay_page = overlay_reader.pages[0]

    template_page.merge_page(overlay_page)

    out = PdfWriter()
    out.add_page(template_page)

    final_buf = io.BytesIO()
    out.write(final_buf)

    return final_buf.getvalue()