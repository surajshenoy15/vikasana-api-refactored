from __future__ import annotations

import base64

from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any


def _logo_data_uri() -> str:
    logo_path = (
        Path(__file__).resolve().parent
        / "static"
        / "app-icon-2.png"
    )

    try:
        encoded = base64.b64encode(
            logo_path.read_bytes()
        ).decode("ascii")

        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""


def _safe(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback

    value = str(value).strip()

    if not value:
        return fallback

    return escape(value)


def _format_date(value: Any) -> str:
    if value is None:
        return "—"

    if isinstance(value, (datetime, date)):
        return value.strftime("%d %B %Y")

    return _safe(value)


def render_certificate_verification_page(
    *,
    valid: bool,
    certificate_no: Any = None,
    student_name: Any = None,
    college: Any = None,
    event_title: Any = None,
    category: Any = None,
    issued_at: Any = None,
    message: str | None = None,
) -> str:
    logo_data = _logo_data_uri()


    if valid:
        status_title = "CERTIFICATE VALID"
        status_subtitle = (
            "This certificate is authentic and has been "
            "verified successfully."
        )

        status_color = "#16A34A"
        status_dark = "#15803D"
        status_bg = "#F0FDF4"
        status_border = "#BBF7D0"

        icon = """
        <svg
            width="38"
            height="38"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.4"
            stroke-linecap="round"
            stroke-linejoin="round"
        >
            <path d="M20 6 9 17l-5-5"/>
        </svg>
        """

    else:
        status_title = "CERTIFICATE NOT VALID"
        status_subtitle = (
            message
            or
            "We could not verify this certificate. "
            "The certificate ID or verification signature "
            "may be invalid."
        )

        status_color = "#DC2626"
        status_dark = "#B91C1C"
        status_bg = "#FEF2F2"
        status_border = "#FECACA"

        icon = """
        <svg
            width="36"
            height="36"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.4"
            stroke-linecap="round"
            stroke-linejoin="round"
        >
            <path d="M18 6 6 18"/>
            <path d="m6 6 12 12"/>
        </svg>
        """

    details = ""

    # Never expose certificate details for failed verification.
    if valid:
        details = f"""
        <section class="details-section">
            <div class="section-heading">
                <div>
                    <div class="eyebrow">
                        CERTIFICATE DETAILS
                    </div>

                    <h2>
                        Verified record
                    </h2>
                </div>

                <div class="verified-mini">
                    <svg
                        width="15"
                        height="15"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2.4"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                    >
                        <path d="M20 6 9 17l-5-5"/>
                    </svg>

                    Verified
                </div>
            </div>


            <div class="details-card">

                <div class="detail-row">
                    <div class="detail-icon">
                        <svg viewBox="0 0 24 24">
                            <path d="M20 21a8 8 0 0 0-16 0"/>
                            <circle cx="12" cy="7" r="4"/>
                        </svg>
                    </div>

                    <div class="detail-content">
                        <div class="detail-label">
                            Student
                        </div>

                        <div class="detail-value">
                            {_safe(student_name)}
                        </div>
                    </div>
                </div>


                <div class="divider"></div>


                <div class="detail-row">
                    <div class="detail-icon">
                        <svg viewBox="0 0 24 24">
                            <rect
                                width="18"
                                height="14"
                                x="3"
                                y="5"
                                rx="2"
                            />
                            <path d="M7 9h10"/>
                            <path d="M7 13h6"/>
                        </svg>
                    </div>

                    <div class="detail-content">
                        <div class="detail-label">
                            Certificate Number
                        </div>

                        <div class="detail-value mono">
                            {_safe(certificate_no)}
                        </div>
                    </div>
                </div>


                <div class="divider"></div>


                <div class="detail-row">
                    <div class="detail-icon">
                        <svg viewBox="0 0 24 24">
                            <path d="M3 10h18"/>
                            <path d="M5 6h14"/>
                            <path d="M7 14h10"/>
                            <path d="M9 18h6"/>
                        </svg>
                    </div>

                    <div class="detail-content">
                        <div class="detail-label">
                            Institution
                        </div>

                        <div class="detail-value">
                            {_safe(college)}
                        </div>
                    </div>
                </div>


                <div class="divider"></div>


                <div class="detail-row">
                    <div class="detail-icon">
                        <svg viewBox="0 0 24 24">
                            <path d="M8 21h8"/>
                            <path d="M12 17v4"/>
                            <path d="M7 4h10"/>
                            <path
                                d="M17 4v4a5 5 0 0 1-10 0V4"
                            />
                            <path d="M5 4H3v2a4 4 0 0 0 4 4"/>
                            <path d="M19 4h2v2a4 4 0 0 1-4 4"/>
                        </svg>
                    </div>

                    <div class="detail-content">
                        <div class="detail-label">
                            Event
                        </div>

                        <div class="detail-value">
                            {_safe(event_title)}
                        </div>
                    </div>
                </div>


                <div class="divider"></div>


                <div class="detail-grid">

                    <div class="grid-item">
                        <div class="detail-label">
                            Category
                        </div>

                        <div class="detail-value small">
                            {_safe(category)}
                        </div>
                    </div>

                    <div class="grid-item">
                        <div class="detail-label">
                            Issued On
                        </div>

                        <div class="detail-value small">
                            {_format_date(issued_at)}
                        </div>
                    </div>

                </div>
            </div>
        </section>
        """

    warning = ""

    if not valid:
        warning = """
        <div class="warning-card">
            <div class="warning-icon">
                !
            </div>

            <div>
                <strong>
                    Verification failed
                </strong>

                <p>
                    Do not accept this certificate as an
                    authentic LoRaa Connect record.
                </p>
            </div>
        </div>
        """

    return f"""<!doctype html>
<html lang="en">

<head>
    <meta charset="utf-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <meta
        name="robots"
        content="noindex,nofollow"
    >

    <title>
        Certificate Verification | LoRaa Connect
    </title>

    <style>
        * {{
            box-sizing: border-box;
        }}

        :root {{
            --navy: #071A35;
            --navy-mid: #123B68;
            --blue: #2563EB;
            --bg: #F5F7FB;
            --text: #0F172A;
            --muted: #64748B;
            --border: #E2E8F0;
        }}

        html,
        body {{
            margin: 0;
            padding: 0;
            min-height: 100%;
        }}

        body {{
            font-family:
                Inter,
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                sans-serif;

            background: var(--bg);
            color: var(--text);
            -webkit-font-smoothing: antialiased;
        }}


        /* HEADER */

        .topbar {{
            background: var(--navy);
            color: white;
            padding:
                max(18px, env(safe-area-inset-top))
                20px
                18px;
        }}

        .topbar-inner {{
            width: 100%;
            max-width: 680px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            gap: 11px;
        }}

        .brand-mark {{
            width: 34px;
            height: 34px;

            display: flex;
            align-items: center;
            justify-content: center;

            flex: 0 0 auto;
        }}

        .brand-logo {{
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }}

        .brand {{
            font-size: 15px;
            font-weight: 800;
            letter-spacing: -.2px;
        }}

        .brand-sub {{
            margin-top: 2px;
            color: rgba(255,255,255,.63);
            font-size: 10px;
            font-weight: 600;
        }}


        /* PAGE */

        .page {{
            max-width: 680px;
            margin: 0 auto;
            padding: 22px 16px 40px;
        }}


        /* STATUS */

        .status-card {{
            text-align: center;

            padding: 30px 18px 26px;

            border-radius: 22px;

            background: {status_bg};
            border: 1px solid {status_border};
        }}

        .status-icon {{
            width: 72px;
            height: 72px;

            margin: 0 auto 16px;

            display: flex;
            align-items: center;
            justify-content: center;

            border-radius: 50%;

            color: white;
            background: {status_color};

            box-shadow:
                0 10px 25px
                color-mix(
                    in srgb,
                    {status_color} 18%,
                    transparent
                );
        }}

        .status-label {{
            color: {status_dark};
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 1.5px;
        }}

        .status-title {{
            margin: 7px 0 0;

            color: {status_dark};

            font-size: clamp(23px, 6vw, 30px);
            font-weight: 900;
            letter-spacing: -.7px;
        }}

        .status-sub {{
            max-width: 440px;

            margin: 10px auto 0;

            color: #475569;
            font-size: 13px;
            line-height: 1.55;
            font-weight: 500;
        }}


        /* DETAILS */

        .details-section {{
            margin-top: 25px;
        }}

        .section-heading {{
            margin-bottom: 11px;

            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 12px;
        }}

        .eyebrow {{
            color: var(--blue);
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1.2px;
        }}

        h2 {{
            margin: 4px 0 0;
            font-size: 19px;
            letter-spacing: -.35px;
        }}

        .verified-mini {{
            display: flex;
            align-items: center;
            gap: 5px;

            color: #15803D;
            font-size: 10px;
            font-weight: 800;
        }}

        .details-card {{
            overflow: hidden;

            padding: 5px 16px;

            border-radius: 18px;

            background: white;
            border: 1px solid var(--border);

            box-shadow:
                0 6px 25px rgba(15,23,42,.04);
        }}

        .detail-row {{
            min-height: 70px;

            display: flex;
            align-items: center;
            gap: 13px;
        }}

        .detail-icon {{
            flex: 0 0 auto;

            width: 38px;
            height: 38px;

            display: flex;
            align-items: center;
            justify-content: center;

            border-radius: 11px;

            background: #EFF6FF;
            color: #2563EB;
        }}

        .detail-icon svg {{
            width: 18px;
            height: 18px;

            fill: none;
            stroke: currentColor;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }}

        .detail-content {{
            min-width: 0;
            flex: 1;
        }}

        .detail-label {{
            margin-bottom: 4px;

            color: var(--muted);
            font-size: 9px;
            font-weight: 800;
            letter-spacing: .5px;
            text-transform: uppercase;
        }}

        .detail-value {{
            color: var(--text);
            font-size: 13px;
            font-weight: 750;
            overflow-wrap: anywhere;
        }}

        .detail-value.small {{
            font-size: 12px;
        }}

        .mono {{
            font-family:
                ui-monospace,
                SFMono-Regular,
                Menlo,
                Consolas,
                monospace;
        }}

        .divider {{
            height: 1px;
            background: #EDF1F5;
        }}

        .detail-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
        }}

        .grid-item {{
            padding: 17px 0;
        }}

        .grid-item + .grid-item {{
            padding-left: 18px;
            border-left: 1px solid #EDF1F5;
        }}


        /* INVALID WARNING */

        .warning-card {{
            margin-top: 16px;

            display: flex;
            align-items: flex-start;
            gap: 11px;

            padding: 14px;

            border-radius: 14px;

            background: #FFF7ED;
            border: 1px solid #FED7AA;
        }}

        .warning-icon {{
            flex: 0 0 auto;

            width: 30px;
            height: 30px;

            display: flex;
            align-items: center;
            justify-content: center;

            border-radius: 9px;

            background: #FFEDD5;
            color: #C2410C;

            font-weight: 900;
        }}

        .warning-card strong {{
            color: #9A3412;
            font-size: 12px;
        }}

        .warning-card p {{
            margin: 3px 0 0;

            color: #9A3412;
            font-size: 11px;
            line-height: 1.45;
        }}


        /* TRUST */

        .trust-card {{
            margin-top: 18px;

            padding: 14px;

            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;

            color: var(--navy-mid);

            font-size: 10px;
            font-weight: 700;

            border-radius: 14px;

            background: #EEF4FF;
        }}

        .trust-card svg {{
            width: 16px;
            height: 16px;
        }}

        .footer {{
            margin-top: 22px;
            text-align: center;

            color: #94A3B8;
            font-size: 9px;
            line-height: 1.6;
        }}


        @media (max-width: 430px) {{
            .page {{
                padding-top: 15px;
            }}

            .status-card {{
                border-radius: 18px;
                padding-top: 25px;
            }}

            .status-icon {{
                width: 64px;
                height: 64px;
            }}

            .detail-grid {{
                grid-template-columns: 1fr;
            }}

            .grid-item + .grid-item {{
                padding-left: 0;
                border-left: 0;
                border-top: 1px solid #EDF1F5;
            }}
        }}
    </style>
</head>

<body>

    <header class="topbar">
        <div class="topbar-inner">

            <div class="brand-mark">
                <img
                    src="{logo_data}"
                    alt="LoRaa Connect"
                    class="brand-logo"
                >
            </div>

            <div>
                <div class="brand">
                    LoRaa Connect
                </div>

                <div class="brand-sub">
                    Certificate Verification
                </div>
            </div>

        </div>
    </header>


    <main class="page">

        <section class="status-card">

            <div class="status-icon">
                {icon}
            </div>

            <div class="status-label">
                VERIFICATION RESULT
            </div>

            <h1 class="status-title">
                {escape(status_title)}
            </h1>

            <p class="status-sub">
                {escape(status_subtitle)}
            </p>

        </section>


        {details}

        {warning}


        <div class="trust-card">

            <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.2"
                stroke-linecap="round"
                stroke-linejoin="round"
            >
                <path
                    d="M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3v8Z"
                />

                <path d="m9 12 2 2 4-4"/>
            </svg>

            Verification powered by LoRaa Connect

        </div>


        <footer class="footer">
            Issued and verified through Vikasana Foundation
            <br>
            LoRaa Connect Certificate Verification
        </footer>

    </main>

</body>
</html>
"""
