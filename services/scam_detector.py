"""
scam_detector.py — Fraudulent job posting detection for Career-Ops.

Runs two tiers of checks:
  1. Instant blocks (auto-fail): money requests, early personal data demands,
     known scam job types, guaranteed income promises.
  2. Warning flags (score reduction): personal-email contact, WhatsApp-only,
     vague descriptions, unrealistic salary, missing company details, urgency language.

Scores start at 100 and fall; tier thresholds:
  ≥80 → safe | ≥60 → caution | ≥40 → suspicious | <40 → scam
"""

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)


# ── Instant-block pattern groups ───────────────────────────────────────────────

INSTANT_BLOCKS: dict[str, list[str]] = {
    "money_request": [
        r"pay.*(?:registration|training|materials|kit|fee)",
        r"(?:send|transfer|wire).*(?:money|\$|₹|usd|inr)",
        r"deposit.*(?:required|needed)",
        r"upfront.*(?:payment|cost|fee)",
        r"(?:western union|moneygram|bitcoin|crypto).*(?:pay|send|transfer)",
    ],
    "personal_data_early": [
        r"(?:passport|ssn|social security|aadhaar|pan card).*(?:required|needed|copy|send)",
        r"send.*(?:id proof|identity proof)",
        r"bank.*(?:details|account number).*(?:needed|required|send)",
    ],
    "guaranteed_income": [
        r"earn.*\$\d{3,}.*(?:week|day|hour)",
        r"(?:no experience|anyone can).*earn.*\$",
        r"guaranteed.*(?:income|job|placement|salary)",
        r"unlimited.*(?:earning potential|income)",
    ],
    "known_scam_jobs": [
        r"(?:data entry|typing).*work from home.*\$\d{2,}",
        r"(?:reshipping|package forwarding).*job",
        r"(?:mystery shopper|secret shopper)",
        r"envelope stuffing",
        r"pyramid.*(?:scheme|plan)",
        r"\bmlm\b.*(?:job|opportunity|earn)",
    ],
}

# ── Warning flag definitions ───────────────────────────────────────────────────

# Each entry: pattern list (may be empty) and optional check function name
_WARNING_FLAGS: dict[str, dict] = {
    "personal_email_contact": {
        "patterns": [
            r"(?:contact|email|apply).*@(?:gmail|yahoo|hotmail|outlook|rediffmail)\.(?:com|in)",
        ],
        "score_penalty": 20,
    },
    "whatsapp_only": {
        "patterns": [
            r"(?:whatsapp|telegram).*only",
            r"(?:contact|reach|apply).*(?:whatsapp|telegram).*number",
        ],
        "score_penalty": 25,
    },
    "urgency_language": {
        "patterns": [
            r"(?:apply|respond).*(?:immediately|today only|right now)",
            r"(?:offer|position).*(?:expires|closing).*(?:today|tonight|tomorrow)",
            r"(?:very few|limited|only \d+).*(?:spots|seats|positions|openings) (?:left|remaining|available)",
        ],
        "score_penalty": 10,
    },
    "generic_company_name": {
        "patterns": [
            r"\b(?:global|international|worldwide|universal)\s+(?:solutions|services|group|corp|enterprise)",
            r"\b(?:tech|digital|cyber|smart)\s+(?:innovations|systems|ventures|hub)\b",
        ],
        "score_penalty": 10,
    },
    "vague_description": {
        "patterns": [],
        "check_func": "_check_vague_description",
        "score_penalty": 15,
    },
    "unrealistic_salary": {
        "patterns": [],
        "check_func": "_check_unrealistic_salary",
        "score_penalty": 20,
    },
    "missing_company_details": {
        "patterns": [],
        "check_func": "_check_missing_company",
        "score_penalty": 15,
    },
}

# ── Salary sanity benchmarks ───────────────────────────────────────────────────

_INDIA_MAX_REASONABLE_LPA = 200       # 200 LPA is very senior exec
_USD_MAX_REASONABLE = 500_000         # $500K/yr


class ScamDetector:
    """Detect fraudulent job postings using pattern matching and heuristics."""

    def check_posting(
        self,
        jd_text: str,
        company: str = "",
        salary: str = "",
    ) -> dict:
        """Run all scam checks on a job posting.

        Args:
            jd_text: Full job description text.
            company: Company name (extracted from JD or metadata).
            salary: Salary string as it appears in the posting.

        Returns:
            {
                safe: bool,
                tier: 'safe' | 'caution' | 'suspicious' | 'scam',
                score: int (0–100),
                red_flags: list[dict],
                warnings: list[dict],
                recommendations: list[str],
            }
        """
        result: dict = {
            "safe": True,
            "tier": "safe",
            "score": 100,
            "red_flags": [],
            "warnings": [],
            "recommendations": [],
        }

        jd_lower = (jd_text or "").lower()

        # ── Instant blocks ─────────────────────────────────────────────────────
        for category, patterns in INSTANT_BLOCKS.items():
            for pattern in patterns:
                if re.search(pattern, jd_lower, re.IGNORECASE):
                    result["safe"] = False
                    result["tier"] = "scam"
                    result["score"] = 0
                    result["red_flags"].append({
                        "category": category,
                        "severity": "critical",
                        "message": f"Detected: {category.replace('_', ' ').title()}",
                        "pattern": pattern,
                    })
                    result["recommendations"].append("Do not apply — likely scam")
                    logger.warning(f"[scam] Instant block: {category} (pattern: {pattern})")
                    return result  # immediate exit

        # ── Warning flags ──────────────────────────────────────────────────────
        for flag_name, config in _WARNING_FLAGS.items():
            triggered = False

            for pat in config.get("patterns", []):
                if re.search(pat, jd_lower, re.IGNORECASE):
                    triggered = True
                    break

            if not triggered and config.get("check_func"):
                fn: Callable | None = getattr(self, config["check_func"], None)
                if fn and fn(jd_text, company, salary):
                    triggered = True

            if triggered:
                penalty = config.get("score_penalty", 10)
                result["score"] -= penalty
                result["warnings"].append({
                    "flag": flag_name,
                    "penalty": penalty,
                    "message": flag_name.replace("_", " ").title(),
                })
                logger.debug(f"[scam] Warning flag: {flag_name} (−{penalty})")

        # ── Final tier ─────────────────────────────────────────────────────────
        score = result["score"]
        if score >= 80:
            result["tier"] = "safe"
        elif score >= 60:
            result["tier"] = "caution"
            result["recommendations"].append("Verify company details before applying")
            result["recommendations"].append("Check company on LinkedIn / Glassdoor")
        elif score >= 40:
            result["tier"] = "suspicious"
            result["safe"] = False
            result["recommendations"].append("High risk — verify company extensively before proceeding")
            result["recommendations"].append("Never share personal or financial details")
        else:
            result["tier"] = "scam"
            result["safe"] = False
            result["recommendations"].append("Do not apply — likely scam")

        return result

    # ── Heuristic check functions ──────────────────────────────────────────────

    def _check_vague_description(self, jd_text: str, company: str, salary: str) -> bool:
        if not jd_text:
            return True
        if len(jd_text.split()) < 100:
            return True
        specifics = {"years", "experience", "degree", "skills",
                     "responsibilities", "requirements", "qualifications"}
        return not any(s in jd_text.lower() for s in specifics)

    def _check_unrealistic_salary(self, jd_text: str, company: str, salary: str) -> bool:
        if not salary:
            return False
        digits = re.findall(r"\d[\d,]*", salary)
        if not digits:
            return False
        nums = [int(d.replace(",", "")) for d in digits if d.replace(",", "").isdigit()]
        if not nums:
            return False
        max_num = max(nums)
        sal_lower = salary.lower()
        if "lpa" in sal_lower or "₹" in sal_lower or "inr" in sal_lower:
            return max_num > _INDIA_MAX_REASONABLE_LPA
        if "usd" in sal_lower or "$" in sal_lower:
            return max_num > _USD_MAX_REASONABLE
        return False

    def _check_missing_company(self, jd_text: str, company: str, salary: str) -> bool:
        if not company or len(company.strip()) < 3:
            return True
        placeholders = {"company", "client", "confidential", "undisclosed", "our client"}
        return company.strip().lower() in placeholders


# ── Singleton ──────────────────────────────────────────────────────────────────
scam_detector = ScamDetector()
