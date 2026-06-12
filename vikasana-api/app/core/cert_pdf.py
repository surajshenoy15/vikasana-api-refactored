# app/core/cert_pdf.py

import io
from datetime import datetime, timezone
from typing import Optional, List, Tuple

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

Segment = Tuple[str, str]  # (text, font_name)


def _safe_text(value, fallback: str = "") -> str:
    value = "" if value is None else str(value).strip()
    return value if value else fallback


def _format_issue_date(value) -> str:
    """
    Converts date/datetime/YYYY-MM-DD string to dd.mm.yyyy.
    """
    if not value:
        return datetime.now(timezone.utc).strftime("%d.%m.%Y")

    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")

    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")

    value = str(value).strip()

    # Convert 2026-06-09 -> 09.06.2026
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        return parsed.strftime("%d.%m.%Y")
    except Exception:
        return value


def _make_qr_image(verify_url: str) -> ImageReader:
    qr = qrcode.QRCode(box_size=3, border=1)
    qr.add_data(verify_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    qr_buf = io.BytesIO()
    img.save(qr_buf, format="PNG")
    qr_buf.seek(0)

    return ImageReader(qr_buf)


def _split_segments_into_words(segments: List[Segment]) -> List[Segment]:
    """
    Keeps font style while splitting text into words.
    """
    out: List[Segment] = []

    for text, font_name in segments:
        words = str(text).split(" ")
        for word in words:
            if word:
                out.append((word, font_name))

    return out


def _draw_rich_paragraph(
    c: canvas.Canvas,
    *,
    segments: List[Segment],
    x: float,
    y: float,
    max_width: float,
    font_size: int = 11,
    leading: float = 17,
    first_line_indent: float = 9 * mm,
) -> float:
    """
    Draws a paragraph with mixed regular/bold text and returns updated y.
    """

    words = _split_segments_into_words(segments)

    if not words:
        return y

    lines: List[List[Segment]] = []
    current_line: List[Segment] = []
    current_width = 0
    is_first_line = True

    for word, font_name in words:
        word_width = stringWidth(word, font_name, font_size)
        space_width = stringWidth(" ", font_name, font_size)

        allowed_width = max_width - (first_line_indent if is_first_line else 0)
        add_width = word_width if not current_line else word_width + space_width

        if current_line and current_width + add_width > allowed_width:
            lines.append(current_line)
            current_line = [(word, font_name)]
            current_width = word_width
            is_first_line = False
        else:
            current_line.append((word, font_name))
            current_width += add_width

    if current_line:
        lines.append(current_line)

    for line_index, line in enumerate(lines):
        cursor_x = x + (first_line_indent if line_index == 0 else 0)

        for word_index, (word, font_name) in enumerate(line):
            c.setFont(font_name, font_size)
            c.setFillColor(black)

            if word_index > 0:
                cursor_x += stringWidth(" ", font_name, font_size)

            c.drawString(cursor_x, y, word)
            cursor_x += stringWidth(word, font_name, font_size)

        y -= leading

    return y


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
) -> List[List[Segment]]:
    """
    Rich paragraphs with bold keywords.
    """

    student_name = _safe_text(student_name, "Student")
    usn = _safe_text(usn)
    college_name = _safe_text(college_name, "this institution")
    event_title = _safe_text(event_title, "the activity")
    venue_name = _safe_text(venue_name, "the event venue")
    event_month_year = _safe_text(event_month_year, "the event period")
    academic_year = _safe_text(academic_year, "the academic year")
    activity_type = _safe_text(activity_type, "Social Activity")
    activity_points = int(activity_points or 0)

    student_with_usn = f"{student_name}, USN {usn}" if usn else student_name

    R = "Times-Roman"
    B = "Times-Bold"

    return [
        [
            ("This is to inform that student ", R),
            (student_with_usn, B),
            (" bearing, has actively participated in the activity ", R),
            (", organized by ", R),
            ("BNMIT", B),
            (" and ", R),
            ("Vikasana Foundation", B),
            (" at ", R),
            (venue_name, B),
            (" in the month of ", R),
            (event_month_year, B),
            (".", R),
        ],
        [
            ("This initiative was designed to encourage student participation in social, educational, environmental, and community development activities. The activity falls under the ", R),
            ("AICTE Activity Point Programme", B),
            (" and aligns with the objectives of experiential learning, social responsibility, community involvement, and nation-building.", R),
        ],
        [
            ("This activity is recognized under the category of ", R),
            (activity_type, B),
            (". It aims to raise awareness about the importance of community participation and responsible citizenship, highlighting the role of students in contributing towards meaningful social impact and public welfare.", R),
        ],
        [
    ("During this period, the participant demonstrated dedication, teamwork, discipline, and active involvement, significantly contributing to the successful completion of the activity.", R),
        ],
        [
            ("In recognition of their efforts, the participant is awarded ", R),
            (f"{activity_points} points", B),
            (" for their valuable contribution to the community and for promoting active participation in socially relevant initiatives.", R),
        ],
        [
            ("We extol Mr./Ms. ", R),
            (student_with_usn, B),
            (" for their enthusiasm, hard work, and commitment to this noble cause. We look forward to their continued participation in future initiatives and wish them success in all their future endeavours.", R),
        ],
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
    Creates transparent overlay PDF with certificate text + QR.
    Managing Trustee text is NOT drawn here because template already has it.
    """

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    w, h = page_size

    certificate_no = _safe_text(certificate_no)
    issue_date = _format_issue_date(issue_date)

    # -----------------------------------------------------
    # Certificate No + Date
    # Moved down to avoid overlapping Vikasana logo/header.
    # -----------------------------------------------------
    top_y = h - 44 * mm

    c.setFillColor(black)
    c.setFont("Times-Roman", 10.5)

    c.drawString(
        18 * mm,
        top_y,
        f"Certificate No: {certificate_no}",
    )

    c.drawRightString(
        w - 18 * mm,
        top_y,
        f"Date: {issue_date}",
    )

    # -----------------------------------------------------
    # Main body spacing
    # -----------------------------------------------------
    x = 20 * mm
    max_width = w - 40 * mm

    # Adjusted slightly down because cert no/date moved down
    y = h - 70 * mm

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
        y = _draw_rich_paragraph(
            c,
            segments=paragraph,
            x=x,
            y=y,
            max_width=max_width,
            font_size=11,
            leading=17,
            first_line_indent=9 * mm,
        )

        y -= 6.8 * mm

    # -----------------------------------------------------
    # QR verification
    # Moved upward so it does not touch the footer/contact area.
    # -----------------------------------------------------
    if verify_url:
        qr_size = 22 * mm
        qr_x = 18 * mm
        qr_y = 34 * mm

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

        label = "Scan QR to verify"
        label_width = stringWidth(label, "Times-Roman", 7)

        c.drawString(
            qr_x + (qr_size - label_width) / 2,
            qr_y - 4 * mm,
            label,
        )

    # -----------------------------------------------------
    # Small digital verification signature
    # -----------------------------------------------------
    if signature:
        c.setFont("Times-Roman", 5)
        c.setFillColor(HexColor("#777777"))

        sig_text = f"Digital Verification Signature: {signature}"
        max_sig_chars = 115

        if len(sig_text) > max_sig_chars:
            sig_text = sig_text[:max_sig_chars] + "..."

        c.drawString(18 * mm, 20 * mm, sig_text)

    c.save()
    return buf.getvalue()
# ---------------------------------------------------------
# Public function
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

    # DB-driven fields
    college_name: str = "this institution",
    event_title: str = "the activity",
    event_month_year: str = "the event period",
    academic_year: str = "the academic year",

    # Optional digital signature/hash
    signature: Optional[str] = None,
) -> bytes:
    """
    Loads the blank certificate template PDF and merges dynamic certificate text.
    """

    template_reader = PdfReader(template_pdf_path)

    if not template_reader.pages:
        raise ValueError("Certificate template PDF has no pages.")

    template_page = template_reader.pages[0]

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