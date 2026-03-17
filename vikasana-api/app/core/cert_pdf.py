# app/core/cert_pdf.py
import io
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter


def _make_overlay_pdf(
    *,
    certificate_no: str,
    issue_date: str,
    student_name: str,
    usn: str,
    activity_type: str,
    venue_name: str,          # ✅ NEW
    activity_points: int,     # ✅ NEW
    verify_url: str,
    page_size=A4,
) -> bytes:
    """Creates a transparent overlay PDF with text + QR only."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    w, h = page_size

    # Title
    c.setFont("Times-Bold", 28)
    c.drawCentredString(w / 2, h - 95 * mm, "CERTIFICATE")

    # Certificate number + date
    c.setFont("Times-Roman", 11)
    c.drawString(22 * mm, h - 65 * mm, f"Certificate No: {certificate_no}")
    c.drawRightString(w - 22 * mm, h - 65 * mm, f"Date: {issue_date}")

    # Main text (center)
    y = h - 120 * mm
    lines = [
        "This is to certify that",
        f"{student_name} (USN: {usn})",
        "has successfully completed the social activity",
        f"“{activity_type}”",
        f"Venue: {venue_name}",                       # ✅ Venue Name from admin event form
        f"Activity Points Awarded: {activity_points}", # ✅ Points
    ]

    for i, line in enumerate(lines):
        bold = i in (1, 3)  # student line + activity line bold
        c.setFont("Times-Bold" if bold else "Times-Roman", 16 if bold else 13)
        c.drawCentredString(w / 2, y - i * 10 * mm, line)

    # QR (bottom-right above footer)
    qr = qrcode.QRCode(box_size=3, border=1)
    qr.add_data(verify_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    qr_buf = io.BytesIO()
    img.save(qr_buf, format="PNG")
    qr_buf.seek(0)

    qr_size = 30 * mm
    qr_img = ImageReader(qr_buf)
    c.drawImage(qr_img, w - 22 * mm - qr_size, 22 * mm, qr_size, qr_size, mask="auto")

    c.setFont("Times-Roman", 8)
    c.drawRightString(w - 22 * mm, 18 * mm, "Scan QR to verify")

    c.save()
    return buf.getvalue()


def build_certificate_pdf(
    *,
    template_pdf_path: str,   # ✅ path to your template PDF
    certificate_no: str,
    issue_date: str,
    student_name: str,
    usn: str,
    activity_type: str,
    venue_name: str,          # ✅ NEW
    activity_points: int,     # ✅ NEW
    verify_url: str,
) -> bytes:
    """
    Loads the template PDF and merges an overlay (text+QR) onto page 1.
    Returns final PDF bytes.
    """
    template_reader = PdfReader(template_pdf_path)
    template_page = template_reader.pages[0]

    # Use the template page size so overlay matches perfectly
    w = float(template_page.mediabox.width)
    h = float(template_page.mediabox.height)

    overlay_bytes = _make_overlay_pdf(
        certificate_no=certificate_no,
        issue_date=issue_date,
        student_name=student_name,
        usn=usn,
        activity_type=activity_type,
        venue_name=venue_name,
        activity_points=activity_points,
        verify_url=verify_url,
        page_size=(w, h),
    )

    overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
    overlay_page = overlay_reader.pages[0]

    # Merge overlay onto template
    template_page.merge_page(overlay_page)

    out = PdfWriter()
    out.add_page(template_page)

    final_buf = io.BytesIO()
    out.write(final_buf)
    return final_buf.getvalue()