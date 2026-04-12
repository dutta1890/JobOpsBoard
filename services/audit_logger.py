"""
audit_logger.py — Security and activity audit logging for Career-Ops.

Appends structured JSONL entries to a daily rotating log file under logs/.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_DIR = Path("logs")


class AuditLogger:
    """Logs all agent activities for security and debugging."""

    def __init__(self):
        LOG_DIR.mkdir(exist_ok=True)

    def _log_file(self) -> Path:
        return LOG_DIR / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"

    def log(self, action: str, details: dict, user_ip: str | None = None) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
            "ip": user_ip,
        }
        try:
            with open(self._log_file(), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.warning(f"Audit log write failed: {exc}")

    # ── Convenience methods ────────────────────────────────────────────────────

    def log_login(self, success: bool, ip: str) -> None:
        self.log("login_attempt", {"success": success}, ip)

    def log_evaluation(self, url: str, grade: str, company: str) -> None:
        self.log("job_evaluation", {"url": url, "grade": grade, "company": company})

    def log_application(self, company: str, method: str, success: bool) -> None:
        self.log("application_sent", {"company": company, "method": method, "success": success})

    def log_scam_detected(self, url: str, tier: str, red_flags: list) -> None:
        self.log("scam_detected", {"url": url, "tier": tier, "red_flags": red_flags})

    def log_agent_action(self, action: str, target: str) -> None:
        self.log("agent_action", {"action": action, "target": target})


# ── Singleton ──────────────────────────────────────────────────────────────────
audit_logger = AuditLogger()
