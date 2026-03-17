from fastapi import APIRouter, Query, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.cert_sign import verify_sig
from app.features.certificates.models import Certificate

# ✅ MUST MATCH QR URL: /api/public/certificates/verify
router = APIRouter(prefix="/public/certificates", tags=["Public - Certificates"])


def _fmt(dt):
    try:
        return dt.strftime("%d %b %Y, %I:%M %p") if dt else "—"
    except Exception:
        return str(dt) if dt else "—"


def _page(title: str, status: str, subtitle: str, rows_html: str) -> str:
    color = "#22c55e" if status == "VALID" else ("#f59e0b" if status == "NOT VALID" else "#ef4444")
    icon = "✓" if status == "VALID" else ("!" if status == "NOT VALID" else "✕")

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{title}</title>
  <style>
    body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:#0b1220;color:#fff;}}
    .wrap{{max-width:880px;margin:0 auto;padding:22px 14px;}}
    .card{{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:18px;overflow:hidden;}}
    .top{{padding:18px;border-bottom:1px solid rgba(255,255,255,.12);display:flex;gap:14px;align-items:flex-start;}}
    .icon{{width:46px;height:46px;border-radius:14px;display:grid;place-items:center;border:1px solid rgba(255,255,255,.12);
          background:rgba(255,255,255,.05);font-size:22px;color:{color};}}
    h1{{margin:0;font-size:18px;}}
    .sub{{margin-top:6px;color:rgba(255,255,255,.72);font-size:13px;line-height:1.4;}}
    .pill{{margin-left:auto;padding:8px 12px;border-radius:999px;background:rgba(255,255,255,.08);
          border:1px solid rgba(255,255,255,.12);font-size:12px;color:rgba(255,255,255,.78);white-space:nowrap;}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px 18px 18px;}}
    @media(max-width:720px){{.grid{{grid-template-columns:1fr;}}}}
    .box{{border:1px solid rgba(255,255,255,.12);border-radius:16px;background:rgba(255,255,255,.04);padding:12px;}}
    .box h3{{margin:0 0 10px;font-size:12px;color:rgba(255,255,255,.70);text-transform:uppercase;letter-spacing:.2px;}}
    .row{{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-top:1px dashed rgba(255,255,255,.12);}}
    .row:first-of-type{{border-top:none;}}
    .k{{color:rgba(255,255,255,.70);font-size:13px;}}
    .v{{font-size:13px;text-align:right;word-break:break-word;}}
    code{{padding:2px 6px;border-radius:8px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.10);}}
    .footer{{padding:14px 18px 18px;border-top:1px solid rgba(255,255,255,.12);color:rgba(255,255,255,.65);font-size:12.5px;}}
  </style>
</head>
<body>
  <div class="wrap">
    <div style="margin-bottom:12px;">
      <div style="display:flex;align-items:center;gap:10px;">
        <div style="width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,rgba(59,130,246,.85),rgba(34,197,94,.75));"></div>
        <div>
          <div style="font-size:14px;font-weight:600;">Vikasana Foundation</div>
          <div style="font-size:12.5px;color:rgba(255,255,255,.65);">Certificate Verification</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="top">
        <div class="icon">{icon}</div>
        <div>
          <h1 style="color:{color};margin:0;">{status}</h1>
          <div class="sub">{subtitle}</div>
        </div>
        <div class="pill">Public Verify</div>
      </div>

      <div class="grid">
        {rows_html}
      </div>

      <div class="footer">
        If this shows INVALID / NOT VALID, the link may be tampered or the certificate may be revoked/not found.
      </div>
    </div>
  </div>
</body>
</html>"""


@router.get("/verify", response_class=HTMLResponse)
async def verify_certificate(
    cert_id: str = Query(...),
    sig: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    # ✅ signature must validate against the SAME cert_id string
    if not verify_sig(cert_id, sig):
        rows = f"""
        <div class="box">
          <h3>Provided</h3>
          <div class="row"><div class="k">cert_id</div><div class="v"><code>{cert_id}</code></div></div>
        </div>
        <div class="box">
          <h3>Reason</h3>
          <div class="row"><div class="k">Signature</div><div class="v">Mismatch</div></div>
        </div>
        """
        return HTMLResponse(_page("Certificate Verify", "INVALID", "Signature verification failed. The URL may be altered.", rows), status_code=400)

    stmt = (
        select(Certificate)
        .options(selectinload(Certificate.student), selectinload(Certificate.event))
        .where(Certificate.certificate_no == cert_id)
    )
    res = await db.execute(stmt)
    cert = res.scalar_one_or_none()

    if not cert or cert.revoked_at is not None:
        reason = "Revoked" if (cert and cert.revoked_at is not None) else "Not found"
        rows = f"""
        <div class="box">
          <h3>Lookup</h3>
          <div class="row"><div class="k">cert_id</div><div class="v"><code>{cert_id}</code></div></div>
          <div class="row"><div class="k">Result</div><div class="v">{reason}</div></div>
        </div>
        <div class="box">
          <h3>Next Steps</h3>
          <div class="row"><div class="k">Action</div><div class="v">Contact admin for re-issue</div></div>
        </div>
        """
        return HTMLResponse(_page("Certificate Verify", "NOT VALID", "Signature is correct, but certificate is not valid in records.", rows), status_code=200)

    s = cert.student
    e = cert.event

    rows = f"""
    <div class="box">
      <h3>Certificate</h3>
      <div class="row"><div class="k">Certificate No</div><div class="v"><code>{cert.certificate_no}</code></div></div>
      <div class="row"><div class="k">Issued At</div><div class="v">{_fmt(cert.issued_at)}</div></div>
      <div class="row"><div class="k">Event</div><div class="v">{getattr(e, "title", None) or getattr(e, "name", None) or "—"}</div></div>
    </div>
    <div class="box">
      <h3>Student</h3>
      <div class="row"><div class="k">Name</div><div class="v">{getattr(s, "name", None) or "—"}</div></div>
      <div class="row"><div class="k">USN</div><div class="v">{getattr(s, "usn", None) or "—"}</div></div>
      <div class="row"><div class="k">College</div><div class="v">{getattr(s, "college", None) or "—"}</div></div>
      <div class="row"><div class="k">Branch</div><div class="v">{getattr(s, "branch", None) or "—"}</div></div>
    </div>
    """
    return HTMLResponse(_page("Certificate Verify", "VALID", "This certificate is authentic and verified.", rows), status_code=200)