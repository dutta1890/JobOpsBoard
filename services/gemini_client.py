"""
gemini_client.py — Job filters, evaluation, and CV generation for Career-Ops.

Uses the google-genai SDK (new, unified SDK) with gemini-2.5-flash.
Pre-filters run *before* any AI call to avoid wasting API quota.
"""

import json
import logging
import os
import re
import time
from functools import wraps

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ── SDK client ────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"
MAX_TOKENS = 8192

def _get_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set.")
    return genai.Client(api_key=GEMINI_API_KEY)


# ── Rate limiting (Gemini free tier: 60 req/min) ─────────────────────────────
_LAST_REQUEST: float = 0.0
_MIN_INTERVAL: float = 1.0


def rate_limited(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _LAST_REQUEST
        elapsed = time.time() - _LAST_REQUEST
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        result = func(*args, **kwargs)
        _LAST_REQUEST = time.time()
        return result
    return wrapper


# ═════════════════════════════════════════════════════════════════════════════
# JOB FILTERS  (run BEFORE AI evaluation to save quota)
# ═════════════════════════════════════════════════════════════════════════════

JOB_AGE_SKIP_DAYS = 7
JOB_AGE_PRIORITY_DAYS = 3

SKIP_TITLES = [
    "intern", "internship", "trainee", "fresher",
    "analyst", "executive", "associate", "junior",
    "entry level", "entry-level", "graduate",
]

SKIP_EXPERIENCE_TOKENS = [
    "0-1", "1-2", "2-3", "0-2", "1-3", "0-3",
    "fresher", "entry level",
]

_JUNIOR_PATTERNS = [
    r"0\s*[-–]\s*[12]\s*years?",
    r"1\s*[-–]\s*[23]\s*years?",
    r"2\s*[-–]\s*3\s*years?",
]


def check_job_age(posted_date_str: str) -> dict:
    """Return age-based filter result.

    Returns:
        dict with keys: skip (bool), reason (str|None), priority (str), days_old (int|None)
    """
    if not posted_date_str:
        return {"skip": False, "priority": "medium", "days_old": None}

    text = posted_date_str.lower()
    days_old = None

    if "just now" in text or "today" in text:
        days_old = 0
    elif "yesterday" in text:
        days_old = 1
    elif re.search(r"\d+\s*hour", text):
        days_old = 0
    elif m := re.search(r"(\d+)\s*day", text):
        days_old = int(m.group(1))
    elif m := re.search(r"(\d+)\s*week", text):
        days_old = int(m.group(1)) * 7
    elif m := re.search(r"(\d+)\s*month", text):
        days_old = int(m.group(1)) * 30

    if days_old is None:
        return {"skip": False, "priority": "medium", "days_old": None}

    if days_old >= JOB_AGE_SKIP_DAYS:
        return {
            "skip": True,
            "reason": f"Job is {days_old} days old (limit: {JOB_AGE_SKIP_DAYS})",
            "priority": "skip",
            "days_old": days_old,
        }

    priority = "high" if days_old <= JOB_AGE_PRIORITY_DAYS else "medium"
    return {"skip": False, "priority": priority, "days_old": days_old, "reason": None}


def check_seniority(title: str, experience_text: str = "", description: str = "") -> dict:
    """Return seniority-based filter result.

    Returns:
        dict with keys: skip (bool), reason (str|None)
    """
    combined = f"{title} {experience_text} {description}".lower()
    title_lower = (title or "").lower()

    for word in SKIP_TITLES:
        if word in title_lower:
            return {"skip": True, "reason": f'Title contains "{word}"'}

    for token in SKIP_EXPERIENCE_TOKENS:
        if token in combined:
            return {"skip": True, "reason": f"Junior experience requirement: {token}"}

    for pat in _JUNIOR_PATTERNS:
        if re.search(pat, combined):
            return {"skip": True, "reason": "Junior experience requirement (regex match)"}

    return {"skip": False, "reason": None}


def pre_filter_job(job_data: dict) -> dict:
    """Run all pre-filters.  Call this BEFORE sending to Gemini.

    Args:
        job_data: dict with optional keys:
            posted_date, title, experience_required, description

    Returns:
        dict: proceed (bool), skip_reason (str|None), priority (str),
              filter_type (str|None), days_old (int|None)
    """
    age = check_job_age(job_data.get("posted_date", ""))
    if age["skip"]:
        return {
            "proceed": False,
            "skip_reason": age["reason"],
            "priority": "skip",
            "filter_type": "job_age",
            "days_old": age["days_old"],
        }

    seniority = check_seniority(
        job_data.get("title", ""),
        job_data.get("experience_required", ""),
        job_data.get("description", ""),
    )
    if seniority["skip"]:
        return {
            "proceed": False,
            "skip_reason": seniority["reason"],
            "priority": "skip",
            "filter_type": "seniority",
            "days_old": age.get("days_old"),
        }

    return {
        "proceed": True,
        "skip_reason": None,
        "priority": age["priority"],
        "filter_type": None,
        "days_old": age.get("days_old"),
    }


# ═════════════════════════════════════════════════════════════════════════════
# EVALUATION
# ═════════════════════════════════════════════════════════════════════════════

def evaluate_job(jd_text: str, job_metadata: dict | None = None) -> dict:
    """Evaluate a job description.

    Pipeline order:
      1. URL legitimacy check (blocks dangerous/suspicious sources immediately)
      2. JD text scam-pattern scan
      3. Pre-filters (age + seniority)
      4. Core 10-dimension Gemini/Claude scoring

    Args:
        jd_text: Raw job description text.
        job_metadata: Optional dict with url, posted_date, title,
                      experience_required, description for filtering.

    Returns:
        dict with success/error keys plus filter/legitimacy metadata.
    """
    # ── Layer 0: URL legitimacy ────────────────────────────────────────────────
    url = (job_metadata or {}).get("url", "")
    if url:
        from services.legitimacy_checker import legitimacy_checker
        legit = legitimacy_checker.check_url(url)
        if not legit["legitimate"]:
            blocks = legit.get("blocks", [])
            reason = "; ".join(blocks) if blocks else f"Tier: {legit['tier']}"
            logger.warning(f"[legitimacy] Blocked URL {url} — {reason}")
            return {
                "success": False,
                "filtered": True,
                "filter_type": "legitimacy",
                "skip_reason": f"Suspicious source — {reason}",
                "legitimacy": legit,
                "score": 0,
                "grade": "F",
                "recommendation": "Skip — Suspicious Source",
            }
        # Attach legitimacy data even for passing URLs
        _legitimacy_meta = legit
    else:
        _legitimacy_meta = None

    # ── Layer 0b: Scam detection (rich multi-tier check) ─────────────────────
    from services.scam_detector import scam_detector
    _scam_check = scam_detector.check_posting(
        jd_text,
        company=(job_metadata or {}).get("company", ""),
        salary=(job_metadata or {}).get("salary", ""),
    )
    if not _scam_check["safe"]:
        tier = _scam_check["tier"]
        flags = [f["message"] for f in _scam_check.get("red_flags", [])]
        reason = f"Scam detection ({tier})" + (f": {', '.join(flags)}" if flags else "")
        logger.warning(f"[scam] Blocked — {reason}")
        return {
            "success": False,
            "filtered": True,
            "filter_type": "scam",
            "skip_reason": reason,
            "scam_check": _scam_check,
            "score": 0,
            "grade": "F",
            "recommendation": "Skip — Potential Scam",
        }

    # ── Layer 1: Pre-filter gate ───────────────────────────────────────────────
    if job_metadata:
        pf = pre_filter_job(job_metadata)
        if not pf["proceed"]:
            logger.info(f"[pre-filter] Skipped — {pf['skip_reason']}")
            return {
                "success": False,
                "filtered": True,
                "filter_type": pf["filter_type"],
                "skip_reason": pf["skip_reason"],
                "priority": "skip",
                "score": 0,
                "grade": "F",
                "recommendation": "Skip",
            }

    # Delegate to core evaluator
    from services.claude_client import evaluate_job as _core_evaluate
    result = _core_evaluate(jd_text)

    # Attach priority metadata from filters
    if result.get("success") and job_metadata:
        pf = pre_filter_job(job_metadata)
        result["priority"] = pf.get("priority", "medium")
        result["days_old"] = pf.get("days_old")
    elif result.get("success"):
        result["priority"] = "medium"

    # Attach scam/legitimacy metadata to successful evaluations
    if result.get("success"):
        result["scam_tier"] = _scam_check["tier"]
        result["scam_warnings"] = _scam_check.get("warnings", [])
        result["scam_recommendations"] = _scam_check.get("recommendations", [])
        if _legitimacy_meta:
            result["legitimacy_tier"] = _legitimacy_meta.get("tier", "unknown")
            result["legitimacy_score"] = _legitimacy_meta.get("score", 50)

    return result


# ═════════════════════════════════════════════════════════════════════════════
# CV GENERATION
# ═════════════════════════════════════════════════════════════════════════════

_CV_SYSTEM_PROMPT = """You are a CV tailoring expert for Aishik Dutta, 8-year performance marketing professional.

## AISHIK'S PROFILE
- Experience: 8+ years, Performance Marketing
- Channels: Google Ads, Meta Ads, YouTube Ads, LinkedIn Ads, TikTok Ads (proficient); Amazon Ads, SEO, Email (basic)
- Budget managed: ₹50 Lakhs – 1 Cr/month
- Team led: 1–2 people
- Certifications: Google Ads, Google Analytics, HubSpot

## KEY ACHIEVEMENTS (use these, never fabricate new ones)
1. HealthHub (Al-Futtaim): 24M impressions, 178K clicks, 200–300 leads/month
2. Gulf Pharmacy: 100K AED revenue on 72K spend → 1.38x ROAS
3. Géant Hypermarket: 5M impressions, 21M video views, 70% reach
4. Camelicious: $170K reach, $600K engagement, 5–6K/month Amazon sales
5. Al-Futtaim Engineering: 400+ B2B leads
6. HCG Oncology: 5% conversion rate, 60+ leads/month via SKAG structure

## RULES
- Reorder bullets by relevance to the JD
- Inject JD keywords naturally — never force them
- NEVER fabricate metrics or experience
- Quantify everything possible
- Write in first-person action verbs (Led, Drove, Delivered, Scaled)

## OUTPUT FORMAT (JSON only, no markdown)
{
  "summary": "3–4 sentence tailored professional summary",
  "keywords_injected": ["keyword1", "keyword2"],
  "experience_bullets": [
    "Most relevant achievement first (quantified)",
    "Second most relevant...",
    "..."
  ],
  "skills_to_highlight": ["skill1", "skill2"],
  "cover_letter_opening": "One strong opening paragraph for this specific role"
}"""


@rate_limited
def generate_cv(jd_text: str, evaluation: dict | None = None) -> dict:
    """Generate tailored CV content for a specific JD.

    Args:
        jd_text: Job description text (truncated to 3000 chars).
        evaluation: Optional dict with archetype/role/company from evaluate_job.

    Returns:
        dict with summary, keywords_injected, experience_bullets,
        skills_to_highlight, cover_letter_opening — or error key.
    """
    archetype = ""
    if evaluation and isinstance(evaluation, dict):
        data = evaluation.get("data", evaluation)
        archetype = data.get("archetype", "")
        role = data.get("role", "")
        company = data.get("company", "")
        archetype_line = f"Role: {role} at {company} | Archetype: {archetype}" if archetype else ""
    else:
        archetype_line = ""

    user_content = f"""Tailor Aishik's CV for this job.

{f'## CONTEXT{chr(10)}{archetype_line}' if archetype_line else ''}

## JOB DESCRIPTION
{jd_text[:3000]}

Output valid JSON only."""

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=_CV_SYSTEM_PROMPT,
                max_output_tokens=MAX_TOKENS,
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        return _parse_json(response.text)
    except Exception as e:
        logger.error(f"CV generation error: {e}")
        return {"error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# COVER LETTER GENERATION
# ═════════════════════════════════════════════════════════════════════════════

_COVER_LETTER_SYSTEM_PROMPT = """Write a tailored cover letter for Aishik Dutta.

## AISHIK'S STRENGTHS
- 8+ years performance marketing, ₹50L–1Cr monthly budgets
- HealthHub: 24M impressions, 178K clicks, 200–300 leads/month
- Gulf Pharmacy: 1.38x ROAS (100K AED revenue on 72K spend)
- Géant: 5M impressions, 21M video views, 70% reach
- Camelicious: $600K engagement, 5–6K/month Amazon sales
- HCG Oncology: 5% conversion, 60+ leads/month via SKAG
- International experience: UAE, KSA, Kuwait, Australia, NZ

## STRUCTURE
1. Hook: Connect one specific achievement to their most urgent need (1 sentence, no "I am writing to apply")
2. Body P1: Most relevant achievement with exact metrics
3. Body P2: Why THIS company + secondary proof point
4. Close: Available in 2 months, clear call to action

## RULES
- 250–300 words total
- Professional but warm tone
- Quantify everything possible
- Name the company explicitly
- No generic phrases ("I am writing to apply", "passionate about marketing")

## OUTPUT FORMAT (JSON only, no markdown)
{
  "full_text": "Complete cover letter text ready to copy-paste",
  "hook": "Just the opening sentence",
  "word_count": 275
}"""


@rate_limited
def generate_cover_letter(jd_text: str, evaluation: dict | None = None) -> dict:
    """Generate a tailored cover letter for a specific JD.

    Args:
        jd_text: Job description text (truncated to 2500 chars).
        evaluation: Optional dict with company/role from evaluate_job result.

    Returns:
        dict with full_text, hook, word_count — or error key.
    """
    if evaluation and isinstance(evaluation, dict):
        data = evaluation.get("data", evaluation)
        company = data.get("company", "the company")
        role = data.get("role", "the role")
        archetype = data.get("archetype", "")
        context = f"Company: {company}\nRole: {role}" + (f"\nArchetype: {archetype}" if archetype else "")
    else:
        context = ""

    user_content = f"""Write a cover letter for Aishik Dutta.

{f'## TARGET ROLE{chr(10)}{context}' if context else ''}

## JOB DESCRIPTION
{jd_text[:2500]}

Output valid JSON only."""

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=_COVER_LETTER_SYSTEM_PROMPT,
                max_output_tokens=2048,
                temperature=0.4,
                response_mime_type="application/json",
            ),
        )
        return _parse_json(response.text)
    except Exception as e:
        logger.error(f"Cover letter generation error: {e}")
        return {"error": str(e)}


def _parse_json(text: str) -> dict:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner, inside = [], False
        for line in lines:
            if line.startswith("```") and not inside:
                inside = True
                continue
            if line.startswith("```") and inside:
                break
            if inside:
                inner.append(line)
        text = "\n".join(inner).strip()

    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1 or end == 0:
        return {"error": "No JSON found in response", "raw": text[:300]}
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {e}", "raw": text[:300]}
