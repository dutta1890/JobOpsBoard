"""
form_applicator.py — Automated form filler for Career-Ops.

Fills and submits Google Forms and generic web forms using Playwright.
All profile data is hardcoded for Aishik Dutta.
"""

import logging
import os
import re
import time

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# ── Candidate profile ──────────────────────────────────────────────────────────
_PROFILE = {
    "name": "Aishik Dutta",
    "first_name": "Aishik",
    "last_name": "Dutta",
    "email": "dutta1890@gmail.com",
    "phone": "+91-8741-03-4164",
    "linkedin": "https://linkedin.com/in/aishikduttaoct18",
    "location": "Mumbai, India",
    "experience": "8",
    "current_company": "Alo Creative LLP",
    "current_role": "Performance Marketer",
    "notice_period": "2 months",
    "expected_salary": "15-25 LPA",
}

# ── Field-detection patterns ───────────────────────────────────────────────────
_PATTERNS: dict[str, list[str]] = {
    "name": [r"full\s*name", r"your\s*name", r"^name$"],
    "first_name": [r"first\s*name"],
    "last_name": [r"last\s*name", r"surname"],
    "email": [r"email", r"e-mail"],
    "phone": [r"phone", r"mobile", r"contact\s*number"],
    "linkedin": [r"linkedin"],
    "location": [r"location", r"city"],
    "experience": [r"years?\s*of\s*experience", r"experience"],
    "current_company": [r"current.*company", r"current.*employer", r"employer"],
    "current_role": [r"current.*(role|title|designation)"],
    "notice_period": [r"notice\s*period", r"availability", r"joining"],
    "expected_salary": [r"expected.*(salary|ctc|compensation)", r"salary\s*expectation"],
}

CV_PATH = "data/cv.pdf"


class FormApplicator:
    """Automatically fills and submits Google Forms and generic web forms."""

    def __init__(self):
        self.profile = _PROFILE.copy()
        self._playwright = None
        self._browser = None
        self.context = None
        self.page = None

    # ── Browser lifecycle ──────────────────────────────────────────────────────

    def start_browser(self, headless: bool = True) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self.context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        self.page = self.context.new_page()

    def stop_browser(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None
            self._browser = None
            self.context = None
            self.page = None

    # ── Field detection ────────────────────────────────────────────────────────

    def detect_field(self, label: str) -> str | None:
        """Return the profile key that best matches a form field label."""
        label_lower = label.lower().strip()
        for field, patterns in _PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, label_lower):
                    return field
        return None

    # ── Google Forms ───────────────────────────────────────────────────────────

    def apply_google_form(self, url: str) -> dict:
        """Fill and submit a Google Form at the given URL.

        Returns:
            dict with success (bool), method, and optional error/note.
        """
        try:
            if not self._browser:
                self.start_browser(headless=True)

            self.page.goto(url, wait_until="networkidle", timeout=20_000)
            time.sleep(2)

            items = self.page.query_selector_all("[data-params]")
            filled = 0

            for item in items:
                q_elem = item.query_selector('[role="heading"]')
                if not q_elem:
                    continue

                question = q_elem.inner_text().strip()
                field_type = self.detect_field(question)

                input_elem = item.query_selector('input[type="text"], textarea')
                if input_elem and field_type and field_type in self.profile:
                    input_elem.fill(self.profile[field_type])
                    filled += 1
                    time.sleep(0.2)

                # Relocation / willingness → click Yes
                if re.search(r"relocat|willing|open\s*to", question, re.I):
                    yes_btn = item.query_selector('[data-value="Yes"]')
                    if yes_btn:
                        yes_btn.click()
                        time.sleep(0.2)

            logger.info(f"Google Form: filled {filled} field(s)")

            submit = self.page.query_selector('[type="submit"]')
            if submit:
                submit.click()
                time.sleep(3)

            page_url = self.page.url
            page_content = self.page.content().lower()

            if "formresponse" in page_url or "your response has been recorded" in page_content:
                return {"success": True, "method": "google_form", "fields_filled": filled}

            return {
                "success": True,
                "method": "google_form",
                "note": "Submitted (confirmation not detected)",
                "fields_filled": filled,
            }

        except Exception as e:
            logger.error(f"Google Form error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            self.stop_browser()

    # ── Generic web forms ──────────────────────────────────────────────────────

    def apply_web_form(self, url: str) -> dict:
        """Fill and submit a generic web application form.

        Returns:
            dict with success (bool), method, and optional error/note.
        """
        try:
            if not self._browser:
                self.start_browser(headless=True)

            self.page.goto(url, wait_until="networkidle", timeout=20_000)
            time.sleep(2)

            filled = 0
            labels = self.page.query_selector_all("label")

            for label in labels:
                label_text = label.inner_text().strip()
                field_type = self.detect_field(label_text)

                if field_type and field_type in self.profile:
                    for_attr = label.get_attribute("for")
                    if for_attr:
                        inp = self.page.query_selector(f"#{for_attr}")
                        if inp:
                            inp.fill(self.profile[field_type])
                            filled += 1
                            time.sleep(0.2)

            # CV upload
            file_input = self.page.query_selector('input[type="file"]')
            if file_input and os.path.exists(CV_PATH):
                file_input.set_input_files(CV_PATH)
                logger.info("CV file attached to form upload")
                time.sleep(1)

            logger.info(f"Web form: filled {filled} field(s)")

            submit = self.page.query_selector(
                'button[type="submit"], input[type="submit"]'
            )
            if submit:
                submit.click()
                time.sleep(3)

            return {
                "success": True,
                "method": "web_form",
                "fields_filled": filled,
            }

        except Exception as e:
            logger.error(f"Web form error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            self.stop_browser()


# ── Singleton ──────────────────────────────────────────────────────────────────
form_applicator = FormApplicator()
