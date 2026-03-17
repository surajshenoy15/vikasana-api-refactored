import os
import httpx


# ══════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════

def _brevo_cfg() -> tuple[str, str, str]:
    api_key = os.getenv("SENDINBLUE_API_KEY", "")
    if not api_key:
        raise RuntimeError("SENDINBLUE_API_KEY not configured")
    from_email = os.getenv("EMAIL_FROM", "admin@vikasana.org")
    from_name  = os.getenv("EMAIL_FROM_NAME", "Vikasana Foundation")
    return api_key, from_email, from_name


async def _send(api_key: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Brevo error {r.status_code}: {r.text}")


# ─── Design tokens ────────────────────────────────────────────
#  Navy  : #0B1F4B   (primary brand, headers, buttons)
#  Gold  : #C9952A   (accent line, highlights)
#  Slate : #475569   (body text)
#  Light : #F7F9FC   (page background)
#  White : #FFFFFF   (card background)
# ─────────────────────────────────────────────────────────────

_VIKASANA_LOGO = """
<table cellpadding="0" cellspacing="0" style="margin:0 auto;">
  <tr>
    <td style="padding-right:10px;vertical-align:middle;">
      <!-- Shield mark -->
      <svg width="40" height="40" viewBox="0 0 40 40" fill="none"
           xmlns="http://www.w3.org/2000/svg">
        <rect width="40" height="40" rx="10" fill="#0B1F4B"/>
        <path d="M20 7L32 12V21C32 27.6 26.5 33.4 20 35C13.5 33.4 8 27.6 8 21V12Z"
              fill="none" stroke="#C9952A" stroke-width="1.8" stroke-linejoin="round"/>
        <path d="M14 21L18.5 26.5L26 15"
              stroke="#ffffff" stroke-width="2.2"
              stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </td>
    <td style="vertical-align:middle;">
      <div style="font-size:17px;font-weight:700;color:#0B1F4B;
                  letter-spacing:-.2px;line-height:1.1;">Vikasana</div>
      <div style="font-size:10px;font-weight:500;color:#C9952A;
                  letter-spacing:2px;text-transform:uppercase;">Foundation</div>
    </td>
  </tr>
</table>
"""

def _wrap(body_html: str, from_email: str = "admin@vikasana.org") -> str:
    """
    Prestigious light-theme shell.
    Layout: light grey page → white card → navy header band → gold rule → content → footer.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Vikasana Foundation</title>
</head>
<body style="margin:0;padding:0;background:#EEF2F7;
             font-family:'Segoe UI',Helvetica,Arial,sans-serif;">

  <!-- Page wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:#EEF2F7;padding:48px 16px;">
    <tr>
      <td align="center">

        <!-- Card -->
        <table width="600" cellpadding="0" cellspacing="0"
               style="max-width:600px;width:100%;background:#ffffff;
                      border-radius:4px;overflow:hidden;
                      border:1px solid #D9E2EE;
                      box-shadow:0 2px 16px rgba(11,31,75,0.08);">

          <!-- Navy header band -->
          <tr>
            <td style="background:#0B1F4B;padding:28px 40px;">
              {_VIKASANA_LOGO}
            </td>
          </tr>

          <!-- Gold accent rule -->
          <tr>
            <td style="height:3px;background:#C9952A;font-size:0;line-height:0;">&nbsp;</td>
          </tr>

          {body_html}

          <!-- Divider -->
          <tr>
            <td style="height:1px;background:#E2E8F0;font-size:0;line-height:0;">&nbsp;</td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#F7F9FC;padding:24px 40px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="text-align:center;">
                    <p style="margin:0 0 6px;font-size:11px;color:#94A3B8;line-height:1.7;">
                      © 2026 <strong style="color:#64748b;">Vikasana Foundation</strong>
                      &nbsp;·&nbsp; Social Activity Tracking Platform
                    </p>
                    <p style="margin:0;font-size:11px;color:#B0BEC5;line-height:1.6;">
                      You received this because an administrator added you to our platform.
                      &nbsp;·&nbsp;
                      <a href="mailto:{from_email}"
                         style="color:#94A3B8;text-decoration:underline;">Contact Support</a>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

        </table>
        <!-- /Card -->

      </td>
    </tr>
  </table>

</body>
</html>"""


def _store_buttons(play_url: str, apple_url: str) -> str:
    """
    Light-theme Play Store + App Store buttons.
    Dark pill on white background — clean and professional.
    """
    return f"""
    <tr>
      <td align="center" style="padding:24px 0 8px;">
        <p style="margin:0 0 16px;font-size:11px;font-weight:600;color:#94A3B8;
                  text-transform:uppercase;letter-spacing:1.2px;">
          Download the App
        </p>
        <table cellpadding="0" cellspacing="0">
          <tr>
            <!-- Google Play -->
            <td style="padding-right:8px;">
              <a href="{play_url}" target="_blank"
                 style="display:inline-block;background:#0B1F4B;color:#fff;
                        text-decoration:none;border-radius:6px;
                        border:1px solid #0B1F4B;overflow:hidden;">
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="padding:10px 14px;vertical-align:middle;">
                      <svg width="20" height="20" viewBox="0 0 512 512"
                           xmlns="http://www.w3.org/2000/svg">
                        <path d="M40 28L280 256 40 484V28Z" fill="#00C853"/>
                        <path d="M40 28L280 256 40 484C24 475 14 458 14 440V72C14 54 24 37 40 28Z"
                              fill="#00C853"/>
                        <path d="M280 256L40 28l214 123.5L280 256Z" fill="#FFEB3B"/>
                        <path d="M280 256l-26 110L40 484 280 256Z" fill="#F44336"/>
                        <path d="M280 256l178-103c14 8 23 23 23 40v114c0 17-9 32-23 40L280 256Z"
                              fill="#2196F3"/>
                      </svg>
                    </td>
                    <td style="padding:10px 14px 10px 0;vertical-align:middle;">
                      <div style="font-size:8px;color:#94A3B8;font-weight:500;
                                  letter-spacing:.8px;line-height:1.2;
                                  text-transform:uppercase;">Get it on</div>
                      <div style="font-size:13px;color:#fff;font-weight:700;
                                  line-height:1.3;white-space:nowrap;">Google Play</div>
                    </td>
                  </tr>
                </table>
              </a>
            </td>

            <!-- App Store -->
            <td style="padding-left:8px;">
              <a href="{apple_url}" target="_blank"
                 style="display:inline-block;background:#0B1F4B;color:#fff;
                        text-decoration:none;border-radius:6px;
                        border:1px solid #0B1F4B;overflow:hidden;">
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="padding:10px 14px;vertical-align:middle;">
                      <svg width="20" height="20" viewBox="0 0 814 1000" fill="white"
                           xmlns="http://www.w3.org/2000/svg">
                        <path d="M788.1 340.9c-5.8 4.5-108.2 62.2-108.2 190.5
                                 0 148.4 130.3 200.9 134.2 202.2-.6 3.2-20.7 71.9-68.7 141.9
                                 -42.8 61.6-87.5 123.1-155.5 123.1s-85.5-39.3-164-39.3
                                 c-76.5 0-103.7 40.8-165.9 40.8s-105-42.4-148.2-107
                                 C46.2 791.2 0 666.3 0 546.8 0 343.9 126.4 236.1 250.8 236.1
                                 c66.1 0 121.2 43.4 162.7 43.4 39.5 0 101.1-46 176.3-46
                                 28.5 0 130.9 2.6 198.3 99.2zm-234-181.5
                                 c31.1-36.9 53.1-88.1 53.1-139.3 0-7.1-.6-14.3-1.9-20.1
                                 -50.6 1.9-110.8 33.7-147.1 75.8-28.5 32.4-55.1 83.6-55.1 135.5
                                 0 7.8 1.3 15.6 1.9 18.1 3.2.6 8.4 1.3 13.6 1.3
                                 45.4 0 102.5-30.4 135.5-71.3z"/>
                      </svg>
                    </td>
                    <td style="padding:10px 14px 10px 0;vertical-align:middle;">
                      <div style="font-size:8px;color:#94A3B8;font-weight:500;
                                  letter-spacing:.8px;line-height:1.2;
                                  text-transform:uppercase;">Download on the</div>
                      <div style="font-size:13px;color:#fff;font-weight:700;
                                  line-height:1.3;white-space:nowrap;">App Store</div>
                    </td>
                  </tr>
                </table>
              </a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _otp_digits(otp: str) -> str:
    """Render each OTP digit in a clean bordered box — no dark backgrounds."""
    boxes = "".join([
        f"""<td style="padding:0 5px;">
              <div style="width:46px;height:56px;line-height:56px;text-align:center;
                          font-size:28px;font-weight:700;background:#F7F9FC;
                          border-radius:6px;border:1.5px solid #CBD5E1;
                          color:#0B1F4B;font-family:'Courier New',monospace;">{d}</div>
            </td>"""
        for d in otp
    ])
    return f"""
    <table cellpadding="0" cellspacing="0" style="margin:0 auto;">
      <tr>{boxes}</tr>
    </table>
    """


# ══════════════════════════════════════════════════════════════
#  1. Faculty — Activation / Invite Email
# ══════════════════════════════════════════════════════════════

async def send_activation_email(to_email: str, to_name: str, activate_url: str) -> None:
    api_key, from_email, from_name = _brevo_cfg()
    subject = "Invitation — Activate Your Faculty Account | Vikasana Foundation"

    body = f"""
          <!-- Greeting section -->
          <tr>
            <td style="padding:40px 40px 0;">
              <p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#C9952A;
                        text-transform:uppercase;letter-spacing:1.5px;">
                Faculty Invitation
              </p>
              <h1 style="margin:8px 0 0;font-size:24px;font-weight:700;color:#0B1F4B;
                         line-height:1.3;letter-spacing:-.3px;">
                Welcome, {to_name}
              </h1>
            </td>
          </tr>

          <!-- Thin gold rule under heading -->
          <tr>
            <td style="padding:16px 40px 0;">
              <div style="width:40px;height:2px;background:#C9952A;"></div>
            </td>
          </tr>

          <!-- Body text -->
          <tr>
            <td style="padding:24px 40px 0;">
              <p style="margin:0;font-size:15px;color:#475569;line-height:1.75;">
                You have been invited to join the
                <strong style="color:#0B1F4B;">Vikasana Foundation</strong>
                Social Activity Tracking platform as a Faculty Member.
                Please activate your account using the button below.
              </p>
            </td>
          </tr>

          <!-- Account details box -->
          <tr>
            <td style="padding:24px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="border:1px solid #D9E2EE;border-radius:4px;
                            border-left:3px solid #0B1F4B;">
                <tr>
                  <td style="padding:16px 20px;">
                    <p style="margin:0 0 2px;font-size:10px;font-weight:700;color:#94A3B8;
                               text-transform:uppercase;letter-spacing:1.2px;">Account Details</p>
                    <p style="margin:4px 0 0;font-size:15px;font-weight:600;color:#0B1F4B;">
                      {to_email}
                    </p>
                    <p style="margin:4px 0 0;font-size:13px;color:#64748B;">
                      Role:&nbsp;<span style="color:#C9952A;font-weight:600;">Faculty Member</span>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- CTA Button -->
          <tr>
            <td style="padding:32px 40px 0;">
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:#0B1F4B;border-radius:4px;">
                    <a href="{activate_url}"
                       style="display:inline-block;padding:14px 36px;
                              color:#ffffff;text-decoration:none;
                              font-size:14px;font-weight:600;
                              letter-spacing:.4px;">
                      Activate My Account &rarr;
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Expiry notice -->
          <tr>
            <td style="padding:20px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#FFFBEB;border:1px solid #FDE68A;border-radius:4px;">
                <tr>
                  <td style="padding:12px 16px;">
                    <p style="margin:0;font-size:13px;color:#92400E;line-height:1.6;">
                      <strong>Note:</strong> This activation link expires in
                      <strong>48 hours</strong>. If it has expired, please contact
                      your administrator for a new invitation.
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Fallback URL -->
          <tr>
            <td style="padding:20px 40px 36px;">
              <p style="margin:0;font-size:12px;color:#94A3B8;line-height:1.7;">
                If the button above does not work, copy and paste the link below
                into your browser:<br/>
                <a href="{activate_url}"
                   style="color:#0B1F4B;font-size:11px;word-break:break-all;">
                  {activate_url}
                </a>
              </p>
            </td>
          </tr>
    """

    payload = {
        "sender":      {"name": from_name, "email": from_email},
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "htmlContent": _wrap(body, from_email),
    }
    await _send(api_key, payload)


# ══════════════════════════════════════════════════════════════
#  2. Faculty — OTP Email
# ══════════════════════════════════════════════════════════════

async def send_faculty_otp_email(to_email: str, to_name: str, otp: str) -> None:
    api_key, from_email, from_name = _brevo_cfg()
    subject = "Your Verification Code — Vikasana Foundation"

    body = f"""
          <!-- Greeting -->
          <tr>
            <td style="padding:40px 40px 0;">
              <p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#C9952A;
                        text-transform:uppercase;letter-spacing:1.5px;">
                Account Activation
              </p>
              <h1 style="margin:8px 0 0;font-size:24px;font-weight:700;color:#0B1F4B;
                         line-height:1.3;">
                Verification Code
              </h1>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 40px 0;">
              <div style="width:40px;height:2px;background:#C9952A;"></div>
            </td>
          </tr>

          <tr>
            <td style="padding:24px 40px 0;">
              <p style="margin:0;font-size:15px;color:#475569;line-height:1.75;">
                Hello <strong style="color:#0B1F4B;">{to_name}</strong>,
                please use the verification code below to complete your
                faculty account activation.
              </p>
            </td>
          </tr>

          <!-- OTP box -->
          <tr>
            <td style="padding:32px 40px 0;text-align:center;">
              <p style="margin:0 0 16px;font-size:11px;font-weight:600;color:#94A3B8;
                        text-transform:uppercase;letter-spacing:1.2px;">
                One-Time Passcode
              </p>
              {_otp_digits(otp)}
            </td>
          </tr>

          <!-- Expiry badge -->
          <tr>
            <td style="padding:20px 40px 0;text-align:center;">
              <table cellpadding="0" cellspacing="0" style="margin:0 auto;">
                <tr>
                  <td style="background:#FEF3C7;border:1px solid #FDE68A;
                             border-radius:100px;padding:8px 20px;">
                    <p style="margin:0;font-size:12px;color:#92400E;font-weight:600;">
                      &#x23F1;&nbsp; This code expires in <strong>10 minutes</strong>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Security note -->
          <tr>
            <td style="padding:24px 40px 36px;">
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#F7F9FC;border:1px solid #E2E8F0;
                            border-radius:4px;border-left:3px solid #94A3B8;">
                <tr>
                  <td style="padding:14px 18px;">
                    <p style="margin:0;font-size:13px;color:#64748B;line-height:1.65;">
                      <strong>Security reminder:</strong> Vikasana Foundation will never
                      ask you to share this code with anyone. If you did not request this,
                      please disregard this email — your account remains secure.
                    </p>
                  </td>
                </tr>
              </table>
              <p style="margin:16px 0 0;font-size:12px;color:#94A3B8;">
                Verifying account: <strong>{to_email}</strong>
              </p>
            </td>
          </tr>
    """

    payload = {
        "sender":      {"name": from_name, "email": from_email},
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "htmlContent": _wrap(body, from_email),
    }
    await _send(api_key, payload)


# ══════════════════════════════════════════════════════════════
#  3. Student — Welcome / Download Email
# ══════════════════════════════════════════════════════════════

async def send_student_welcome_email(
    to_email: str,
    to_name: str,
    app_download_url: str,
    *,
    play_store_url: str = "https://play.google.com/store/apps/details?id=org.vikasana",
    app_store_url: str  = "https://apps.apple.com/app/vikasana/id000000000",
) -> None:
    api_key, from_email, from_name = _brevo_cfg()
    subject = "Welcome to Vikasana Foundation — Get Started Today"

    steps = [
        ("01", "Download the Vikasana app from the <strong>Play Store</strong> or <strong>App Store</strong>."),
        ("02", f"Open the app and enter your registered email: <strong>{to_email}</strong>"),
        ("03", "Enter the OTP sent to your inbox — no password needed."),
    ]
    steps_html = "".join([
        f"""<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
              <tr>
                <td width="36" style="vertical-align:top;padding-top:1px;">
                  <div style="width:32px;height:32px;line-height:32px;text-align:center;
                              border-radius:4px;font-size:11px;font-weight:700;
                              color:#ffffff;background:#0B1F4B;
                              font-family:'Courier New',monospace;">{n}</div>
                </td>
                <td style="padding-left:14px;vertical-align:top;
                           border-bottom:1px solid #EEF2F7;padding-bottom:16px;">
                  <p style="margin:0;color:#475569;font-size:14px;line-height:1.65;
                            padding-top:6px;">{t}</p>
                </td>
              </tr>
            </table>"""
        for n, t in steps
    ])

    body = f"""
          <!-- Greeting -->
          <tr>
            <td style="padding:40px 40px 0;">
              <p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#C9952A;
                        text-transform:uppercase;letter-spacing:1.5px;">
                Student Enrollment
              </p>
              <h1 style="margin:8px 0 0;font-size:24px;font-weight:700;color:#0B1F4B;
                         line-height:1.3;letter-spacing:-.2px;">
                Welcome, {to_name}
              </h1>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 40px 0;">
              <div style="width:40px;height:2px;background:#C9952A;"></div>
            </td>
          </tr>

          <!-- Intro -->
          <tr>
            <td style="padding:24px 40px 0;">
              <p style="margin:0;font-size:15px;color:#475569;line-height:1.75;">
                Your faculty has enrolled you in the
                <strong style="color:#0B1F4B;">Vikasana Foundation</strong>
                learning platform. Download the app to begin — login is
                seamless and passwordless via one-time passcode.
              </p>
            </td>
          </tr>

          <!-- Registered email card -->
          <tr>
            <td style="padding:24px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="border:1px solid #D9E2EE;border-radius:4px;
                            border-left:3px solid #C9952A;">
                <tr>
                  <td style="padding:14px 20px;">
                    <p style="margin:0 0 2px;font-size:10px;font-weight:700;color:#94A3B8;
                               text-transform:uppercase;letter-spacing:1.2px;">
                      Your Registered Email
                    </p>
                    <p style="margin:4px 0 0;font-size:15px;font-weight:600;color:#0B1F4B;">
                      {to_email}
                    </p>
                    <p style="margin:4px 0 0;font-size:12px;color:#64748B;">
                      Use this email to log in to the app
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Steps heading -->
          <tr>
            <td style="padding:28px 40px 4px;">
              <p style="margin:0;font-size:11px;font-weight:700;color:#94A3B8;
                        text-transform:uppercase;letter-spacing:1.2px;">
                Getting Started
              </p>
            </td>
          </tr>

          <!-- Steps -->
          <tr>
            <td style="padding:12px 40px 0;">
              {steps_html}
            </td>
          </tr>

          <!-- Store buttons -->
          <table width="100%" cellpadding="0" cellspacing="0">
            {_store_buttons(play_store_url, app_store_url)}
          </table>

          <!-- Fallback URL -->
          <tr>
            <td style="padding:12px 40px 0;text-align:center;">
              <p style="margin:0;font-size:12px;color:#94A3B8;">
                Or download directly:&nbsp;
                <a href="{app_download_url}"
                   style="color:#0B1F4B;font-weight:600;text-decoration:underline;">
                  {app_download_url}
                </a>
              </p>
            </td>
          </tr>

          <!-- Ignore notice -->
          <tr>
            <td style="padding:24px 40px 36px;text-align:center;">
              <p style="margin:0;font-size:12px;color:#B0BEC5;line-height:1.6;">
                If you were not expecting this invitation, you may safely disregard this email.
              </p>
            </td>
          </tr>
    """

    payload = {
        "sender":      {"name": from_name, "email": from_email},
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "htmlContent": _wrap(body, from_email),
    }
    await _send(api_key, payload)


# ══════════════════════════════════════════════════════════════
#  4. Student — OTP Email
# ══════════════════════════════════════════════════════════════

async def send_student_otp_email(to_email: str, to_name: str, otp: str) -> None:
    api_key, from_email, from_name = _brevo_cfg()
    subject = "Your Login Code — Vikasana Foundation"

    body = f"""
          <!-- Greeting -->
          <tr>
            <td style="padding:40px 40px 0;">
              <p style="margin:0 0 4px;font-size:12px;font-weight:600;color:#C9952A;
                        text-transform:uppercase;letter-spacing:1.5px;">
                Student Login
              </p>
              <h1 style="margin:8px 0 0;font-size:24px;font-weight:700;color:#0B1F4B;
                         line-height:1.3;">
                Your Verification Code
              </h1>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 40px 0;">
              <div style="width:40px;height:2px;background:#C9952A;"></div>
            </td>
          </tr>

          <tr>
            <td style="padding:24px 40px 0;">
              <p style="margin:0;font-size:15px;color:#475569;line-height:1.75;">
                Hello <strong style="color:#0B1F4B;">{to_name}</strong>,
                use the passcode below to sign in to your Vikasana account.
              </p>
            </td>
          </tr>

          <!-- OTP -->
          <tr>
            <td style="padding:32px 40px 0;text-align:center;">
              <p style="margin:0 0 16px;font-size:11px;font-weight:600;color:#94A3B8;
                        text-transform:uppercase;letter-spacing:1.2px;">
                One-Time Passcode
              </p>
              {_otp_digits(otp)}
            </td>
          </tr>

          <!-- Expiry -->
          <tr>
            <td style="padding:20px 40px 0;text-align:center;">
              <table cellpadding="0" cellspacing="0" style="margin:0 auto;">
                <tr>
                  <td style="background:#FEF3C7;border:1px solid #FDE68A;
                             border-radius:100px;padding:8px 20px;">
                    <p style="margin:0;font-size:12px;color:#92400E;font-weight:600;">
                      &#x23F1;&nbsp; This code expires in <strong>10 minutes</strong>
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Security note -->
          <tr>
            <td style="padding:24px 40px 36px;">
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#F7F9FC;border:1px solid #E2E8F0;
                            border-radius:4px;border-left:3px solid #94A3B8;">
                <tr>
                  <td style="padding:14px 18px;">
                    <p style="margin:0;font-size:13px;color:#64748B;line-height:1.65;">
                      <strong>Security reminder:</strong> Vikasana Foundation will never
                      ask you to share this code. If you did not initiate this request,
                      please disregard this email — your account is secure.
                    </p>
                  </td>
                </tr>
              </table>
              <p style="margin:14px 0 0;font-size:12px;color:#94A3B8;">
                Signing in as <strong>{to_email}</strong>
              </p>
            </td>
          </tr>
    """

    payload = {
        "sender":      {"name": from_name, "email": from_email},
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "htmlContent": _wrap(body, from_email),
    }
    await _send(api_key, payload)