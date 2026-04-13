"""
agent.py — Career-Ops Autonomous Agent
Processes job applications automatically.
Only pauses when portal login is required.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    WAITING_AUTH = "waiting_auth"
    COMPLETED = "completed"
    FAILED = "failed"


class ApplicationMethod(Enum):
    EMAIL = "email"
    GOOGLE_FORM = "google_form"
    WEB_FORM = "web_form"
    NAUKRI = "naukri"
    INDEED = "indeed"


class Task:
    """Single application task."""

    def __init__(self, app_id, company, role, url, jd_text, method):
        self.id = app_id
        self.company = company
        self.role = role
        self.url = url
        self.jd_text = jd_text
        self.method = method
        self.status = TaskStatus.QUEUED
        self.result = None
        self.error = None
        self.created_at = datetime.now()


class CareerOpsAgent:
    """Autonomous job application agent.

    Runs a background thread that drains a queue of Tasks, routing each
    to the right applicator (email, Google Form, web form, or portal).
    Portal jobs (Naukri / Indeed) pause and wait for manual auth.
    LinkedIn jobs are skipped entirely.
    """

    def __init__(self):
        self.queue: list[Task] = []
        self.is_running = False
        self.current_task: Task | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self.stats = {
            "processed": 0,
            "successful": 0,
            "failed": 0,
            "waiting_auth": 0,
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.is_running:
            logger.info("Agent already running.")
            return
        self.is_running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Agent started.")

    def stop(self) -> None:
        self.is_running = False
        logger.info("Agent stop requested.")

    # ── Queue management ───────────────────────────────────────────────────────

    def add_task(self, app_id, company, role, url, jd_text) -> str | None:
        """Detect the application method and add to queue. Returns task ID or None."""
        method = self._detect_method(url, jd_text)

        if method == "linkedin":
            logger.info(f"Skipping LinkedIn job: {company} – {role}")
            return None

        task = Task(app_id, company, role, url, jd_text, method)
        with self._lock:
            # Avoid duplicate app_id in queue
            existing_ids = {t.id for t in self.queue}
            if app_id in existing_ids:
                logger.info(f"Task {app_id} already queued, skipping.")
                return app_id
            self.queue.append(task)

        logger.info(f"Queued: {company} – {role} ({method.value if hasattr(method, 'value') else method})")
        return app_id

    def resume_task(self, task_id) -> bool:
        """Re-queue a WAITING_AUTH task after the user has logged in."""
        with self._lock:
            for task in self.queue:
                if task.id == task_id and task.status == TaskStatus.WAITING_AUTH:
                    task.status = TaskStatus.QUEUED
                    logger.info(f"Resumed task {task_id}: {task.company}")
                    return True
        return False

    # ── Status ─────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._lock:
            queue_snapshot = list(self.queue)

        return {
            "is_running": self.is_running,
            "current": (
                {"company": self.current_task.company, "role": self.current_task.role}
                if self.current_task
                else None
            ),
            "queue": {
                "total": len(queue_snapshot),
                "queued": sum(1 for t in queue_snapshot if t.status == TaskStatus.QUEUED),
                "processing": sum(1 for t in queue_snapshot if t.status == TaskStatus.PROCESSING),
                "waiting_auth": sum(1 for t in queue_snapshot if t.status == TaskStatus.WAITING_AUTH),
                "completed": sum(1 for t in queue_snapshot if t.status == TaskStatus.COMPLETED),
                "failed": sum(1 for t in queue_snapshot if t.status == TaskStatus.FAILED),
            },
            "stats": dict(self.stats),
            "waiting_auth": [
                {
                    "id": t.id,
                    "company": t.company,
                    "role": t.role,
                    "portal": t.method.value if hasattr(t.method, "value") else str(t.method),
                }
                for t in queue_snapshot
                if t.status == TaskStatus.WAITING_AUTH
            ],
        }

    # ── Internal: background loop ──────────────────────────────────────────────

    def _run(self) -> None:
        while self.is_running:
            task = self._get_next_task()
            if not task:
                time.sleep(3)
                continue

            self.current_task = task
            task.status = TaskStatus.PROCESSING
            logger.info(f"Processing: {task.company} – {task.role}")

            try:
                result = self._process_task(task)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
                logger.exception(f"Unexpected error processing task {task.id}")

            if result.get("waiting_auth"):
                task.status = TaskStatus.WAITING_AUTH
                self.stats["waiting_auth"] += 1
                logger.info(f"Waiting for auth: {task.company} ({result.get('portal')})")
            elif result.get("success"):
                task.status = TaskStatus.COMPLETED
                task.result = result
                self.stats["successful"] += 1
                logger.info(f"Applied: {task.company} – {task.role}")
                self._mark_applied_in_db(task)
                try:
                    from services.audit_logger import audit_logger
                    audit_logger.log_application(
                        company=task.company,
                        method=task.method.value if hasattr(task.method, "value") else str(task.method),
                        success=True,
                    )
                except Exception:
                    pass
            else:
                task.status = TaskStatus.FAILED
                task.error = result.get("error", "Unknown error")
                self.stats["failed"] += 1
                logger.warning(f"Failed: {task.company} – {result.get('error')}")
                try:
                    from services.audit_logger import audit_logger
                    audit_logger.log_application(
                        company=task.company,
                        method=task.method.value if hasattr(task.method, "value") else str(task.method),
                        success=False,
                    )
                except Exception:
                    pass

            self.stats["processed"] += 1
            self.current_task = None
            time.sleep(5)

    def _get_next_task(self) -> Task | None:
        with self._lock:
            for task in self.queue:
                if task.status == TaskStatus.QUEUED:
                    return task
        return None

    def _process_task(self, task: Task) -> dict:
        from services.email_applicator import email_applicator
        from services.form_applicator import form_applicator
        from services.gemini_client import generate_cover_letter

        # Load evaluation from DB
        from models.database import Application
        app_record = Application.query.get(task.id)
        evaluation = {}
        if app_record and app_record.evaluation_json:
            try:
                evaluation = json.loads(app_record.evaluation_json)
            except Exception:
                pass

        if task.method == ApplicationMethod.EMAIL:
            cover = generate_cover_letter(task.jd_text, evaluation)
            if "error" in cover:
                return {"success": False, "error": f"Cover letter: {cover['error']}"}

            to_email = email_applicator.extract_email_from_jd(task.jd_text)
            if not to_email:
                return {"success": False, "error": "No recruiter email found in JD"}

            return email_applicator.apply(
                to_email=to_email,
                company=task.company,
                role=task.role,
                cover_letter_text=cover.get("full_text", ""),
            )

        elif task.method == ApplicationMethod.GOOGLE_FORM:
            return form_applicator.apply_google_form(task.url)

        elif task.method == ApplicationMethod.WEB_FORM:
            return form_applicator.apply_web_form(task.url)

        elif task.method in (ApplicationMethod.NAUKRI, ApplicationMethod.INDEED):
            return {
                "success": False,
                "waiting_auth": True,
                "auth_type": "login",
                "portal": task.method.value,
            }

        return {"success": False, "error": f"Unknown method: {task.method}"}

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_method(url: str, jd_text: str):
        """Return the ApplicationMethod (or 'linkedin' sentinel string)."""
        url_lower = (url or "").lower()
        jd_lower = (jd_text or "").lower()

        if "linkedin.com" in url_lower:
            return "linkedin"

        if "@" in url_lower or "mailto:" in url_lower:
            return ApplicationMethod.EMAIL

        if re.search(r"send.*(cv|resume).*to.*@", jd_lower):
            return ApplicationMethod.EMAIL
        if re.search(r"email.*(cv|resume)", jd_lower):
            return ApplicationMethod.EMAIL

        if "docs.google.com/forms" in url_lower or "forms.gle" in url_lower:
            return ApplicationMethod.GOOGLE_FORM

        if "naukri.com" in url_lower:
            return ApplicationMethod.NAUKRI
        if "indeed.com" in url_lower:
            return ApplicationMethod.INDEED

        return ApplicationMethod.WEB_FORM

    @staticmethod
    def _mark_applied_in_db(task: Task) -> None:
        """Update application status to Applied in the database."""
        try:
            from models.database import Application
            from extensions import db
            app_record = Application.query.get(task.id)
            if app_record:
                app_record.status = "Applied"
                app_record.applied_date = datetime.utcnow()
                db.session.commit()
        except Exception as e:
            logger.warning(f"Could not update DB for task {task.id}: {e}")


# ── Singleton ──────────────────────────────────────────────────────────────────
agent = CareerOpsAgent()
