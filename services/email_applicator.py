"""
email_applicator.py — Automated job application email sender for Career-Ops.

Sends plain-text application emails with optional CV PDF attachment.
Reads credentials from environment secrets set in Replit.
"""

import logging
import os
import re
import smtplib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Candidate constants ────────────────────────────────────────────────────────
SENDER_NAME = "Aishik Dutta"
PHONE = "+91-8741-03-4164"
LINKEDIN = "linkedin.com/in/aishikduttaoct18"
CV_PATH = Path("data/cv.pdf")

# Known-bad placeholder domains to ignore when extracting emails from JDs
_PLACEHOLDER_DOMAINS = {
    "example.com", "email.com", "domain.com", "company.com",
    "yourcompany.com", "test.com", "sample.com",
}


class EmailApplicator:
    """Send application emails automatically via Gmail SMTP."""

    def __init__(self):
        self.smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", 587))
        self.email_address = os.environ.get("EMAIL_ADDRESS", "")
        self.email_password = os.environ.get("EMAIL_APP_PASSWORD", "")

    # ── Public API ─────────────────────────────────────────────────────────────

    def apply(self, to_email: str, company: str, role: str,
              cover_letter_text: str) -> dict:
        """Send an application email with an optional CV attachment.

        Args:
            to_email: Recipient email address.
            company: Company name (used in subject line).
            role: Job role title (used in subject line).
            cover_letter_text: Plain-text cover letter body.

        Returns:
            dict with success (bool), and either error or method/to/timestamp.
        """
        if not to_email:
            return {"success": False, "error": "No recipient email provided."}

        if not self.email_address or not self.email_password:
            return {
                "success": False,
                "error": "Email credentials not configured. "
                         "Set EMAIL_ADDRESS and EMAIL_APP_PASSWORD in Secrets.",
            }

        try:
            msg = self._build_message(to_email, company, role, cover_letter_text)
            self._send(msg)
            logger.info(f"Application sent to {to_email} for {role} at {company}")
            return {
                "success": True,
                "method": "email",
                "to": to_email,
                "company": company,
                "role": role,
                "timestamp": datetime.now().isoformat(),
            }
        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed — check EMAIL_APP_PASSWORD")
            return {
                "success": False,
                "error": "SMTP authentication failed. "
                         "Make sure you are using a Gmail App Password, not your regular password.",
            }
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return {"success": False, "error": str(e)}

    def test_connection(self) -> dict:
        """Verify SMTP credentials without sending an email.

        Returns:
            dict with success (bool) and optional error.
        """
        if not self.email_address or not self.email_password:
            return {"success": False, "error": "Email credentials not configured."}
        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.email_address, self.email_password)
            logger.info("SMTP connection test passed.")
            return {"success": True}
        except smtplib.SMTPAuthenticationError:
            return {
                "success": False,
                "error": "Authentication failed — use a Gmail App Password.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def extract_email_from_jd(jd_text: str) -> str | None:
        """Extract the first valid recruiter email from a job description.

        Args:
            jd_text: Raw job description text.

        Returns:
            Email string if found, else None.
        """
        pattern = r"[\w.\-+]+@[\w.\-]+\.\w{2,}"
        matches = re.findall(pattern, jd_text)
        for email in matches:
            domain = email.split("@", 1)[-1].lower()
            if domain not in _PLACEHOLDER_DOMAINS:
                return email
        return None

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _build_message(self, to_email: str, company: str, role: str,
                       cover_letter_text: str) -> MIMEMultipart:
        msg = MIMEMultipart()
        msg["From"] = f"{SENDER_NAME} <{self.email_address}>"
        msg["To"] = to_email
        msg["Subject"] = f"Application for {role} – {SENDER_NAME}"

        body = (
            f"Dear Hiring Manager,\n\n"
            f"{cover_letter_text}\n\n"
            f"Best regards,\n"
            f"{SENDER_NAME}\n"
            f"{PHONE}\n"
            f"{LINKEDIN}\n\n"
            f"---\nResume attached."
        )
        msg.attach(MIMEText(body, "plain"))

        if CV_PATH.exists():
            try:
                with open(CV_PATH, "rb") as fh:
                    part = MIMEBase("application", "pdf")
                    part.set_payload(fh.read())
                    encoders.encode_base64(part)
                    filename = f"{SENDER_NAME.replace(' ', '_')}_CV.pdf"
                    part.add_header(
                        "Content-Disposition", f'attachment; filename="{filename}"'
                    )
                    msg.attach(part)
                logger.info(f"CV attached from {CV_PATH}")
            except Exception as e:
                logger.warning(f"Could not attach CV: {e}")
        else:
            logger.info(f"CV file not found at {CV_PATH} — sending without attachment")

        return msg

    def _send(self, msg: MIMEMultipart) -> None:
        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(self.email_address, self.email_password)
            server.send_message(msg)


# ── Singleton ──────────────────────────────────────────────────────────────────
email_applicator = EmailApplicator()
