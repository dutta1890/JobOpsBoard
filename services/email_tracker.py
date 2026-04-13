"""
email_tracker.py — IMAP inbox scanner for Career-Ops.

Checks Gmail inbox for replies to job applications and classifies them
as interview invitations, rejections, or general replies.
"""

import email
import imaplib
import logging
import os
from datetime import datetime, timedelta
from email.header import decode_header

logger = logging.getLogger(__name__)

# ── Classification keyword lists ───────────────────────────────────────────────
_INTERVIEW_KW = [
    "interview", "schedule", "next steps", "shortlisted",
    "move forward", "speak with", "call with you",
]
_REJECTION_KW = [
    "unfortunately", "regret", "not moving forward", "other candidates",
    "not selected", "not a match", "decided to proceed", "no longer",
]
_REPLY_KW = [
    "application", "position", "role", "opportunity",
    "thank you", "your cv", "your resume",
]

# Skip emails sent from the candidate's own address
_OWN_EMAIL = os.environ.get("EMAIL_ADDRESS", "").lower()


def _decode_header_value(raw) -> str:
    """Safely decode an RFC 2047 encoded header value."""
    try:
        parts = decode_header(raw or "")
        decoded = []
        for fragment, enc in parts:
            if isinstance(fragment, bytes):
                decoded.append(fragment.decode(enc or "utf-8", errors="replace"))
            else:
                decoded.append(fragment)
        return "".join(decoded)
    except Exception:
        return str(raw or "")


_CACHE_TTL = 300  # seconds — only hit IMAP once every 5 minutes


class EmailTracker:
    """Scan Gmail inbox for job application replies."""

    def __init__(self):
        self.imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
        self.email_address = os.environ.get("EMAIL_ADDRESS", "")
        self.email_password = os.environ.get("EMAIL_APP_PASSWORD", "")
        self._cache: list[dict] | dict | None = None
        self._cache_expires: datetime | None = None
        self._cache_days: int | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def check_replies(self, days: int = 30) -> list[dict] | dict:
        """Scan inbox for job-related replies from the past N days.

        Args:
            days: How many days back to search (default 30).

        Returns:
            List of reply dicts, or dict with 'error' key on failure.
        """
        if not self.email_address or not self.email_password:
            return {"error": "Email credentials not configured."}

        now = datetime.now()
        if (
            self._cache is not None
            and self._cache_expires is not None
            and self._cache_days == days
            and now < self._cache_expires
        ):
            return self._cache

        replies = []

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, timeout=15)
            mail.login(self.email_address, self.email_password)
            mail.select("INBOX")

            since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            _, message_ids = mail.search(None, f"(SINCE {since_date})")

            ids = message_ids[0].split()
            # Scan at most the last 50 messages
            for num in ids[-50:]:
                try:
                    _, msg_data = mail.fetch(num, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    subject = _decode_header_value(msg.get("Subject", ""))
                    from_addr = _decode_header_value(msg.get("From", ""))
                    date_str = msg.get("Date", "")

                    # Skip our own outbound emails
                    if self.email_address.lower() in from_addr.lower():
                        continue

                    subj_lower = subject.lower()

                    is_interview = any(kw in subj_lower for kw in _INTERVIEW_KW)
                    is_rejection = any(kw in subj_lower for kw in _REJECTION_KW)
                    is_reply = any(kw in subj_lower for kw in _REPLY_KW)

                    if is_interview or is_rejection or is_reply:
                        if is_interview:
                            reply_type = "interview"
                        elif is_rejection:
                            reply_type = "rejection"
                        else:
                            reply_type = "reply"

                        replies.append(
                            {
                                "subject": subject,
                                "from": from_addr,
                                "type": reply_type,
                                "date": date_str,
                            }
                        )

                except Exception as msg_err:
                    logger.warning(f"Could not parse message {num}: {msg_err}")
                    continue

            mail.logout()
            logger.info(f"Email check: found {len(replies)} job-related replies")
            self._cache = replies
            self._cache_expires = datetime.now() + timedelta(seconds=_CACHE_TTL)
            self._cache_days = days
            return replies

        except imaplib.IMAP4.error as imap_err:
            logger.error(f"IMAP error: {imap_err}")
            return {"error": f"IMAP authentication failed: {imap_err}"}
        except Exception as e:
            logger.error(f"Email tracker error: {e}")
            return {"error": str(e)}

    def get_summary(self) -> dict:
        """Return counts of interview/rejection/reply emails.

        Returns:
            dict with total, interviews, rejections, other — or error key.
        """
        replies = self.check_replies()

        if isinstance(replies, dict) and "error" in replies:
            return replies

        return {
            "total": len(replies),
            "interviews": sum(1 for r in replies if r["type"] == "interview"),
            "rejections": sum(1 for r in replies if r["type"] == "rejection"),
            "other": sum(1 for r in replies if r["type"] == "reply"),
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
email_tracker = EmailTracker()
