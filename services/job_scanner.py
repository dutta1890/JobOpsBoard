"""
job_scanner.py — Brutally layered, frugal job crawler for Career-Ops.

Filter order (no network/AI wasted on failed layers):
  PRE-0  Empty page / no JD content
  PRE-1  Min text length + job-page structure
  L0     Seniority gate  (BOTH directions: too-junior AND too-senior)
  L1a    PM keyword density  (≥2 groups, ≥3 hits)
  L1b    User keyword cross-check  (at least 1 of scanner_settings.search_keywords)
  L2     Location hard gate  (city in text OR remote/hybrid — no exceptions)
  L3     Role title function check  (non-PM job function → skip)
  ──── Gemini called only if all above pass ────────────────────────────────
  L4     Gemini full evaluation (age, scam, legitimacy, 10-dimension score)
  L5     Grade gate: A or B only
  L6     Post-AI location sanity check

Dynamic discovery: Google Search HTML scraping (no API key required).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, quote, unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE          = os.path.dirname(__file__)
SOURCES_PATH      = os.path.join(BASE, "..", "data", "job_sources.json")
SETTINGS_PATH     = os.path.join(BASE, "..", "data", "scanner_settings.json")
SCAN_MEMORY_PATH  = os.path.join(BASE, "..", "data", "scan_memory.json")

# Hours before re-scanning a source that previously yielded 0 new jobs
SOURCE_COOLDOWN_HOURS = 12

# ── HTTP ───────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection":      "keep-alive",
    "DNT":             "1",
}
REQUEST_TIMEOUT      = 18
DELAY_BETWEEN_PAGES  = 2.5   # seconds between individual page fetches
MAX_JOBS_PER_SOURCE  = 6     # max individual job pages to evaluate per source
SCAN_INTERVAL_HOURS  = 3     # hours between continuous-mode passes (delta)
DISCOVERY_INTERVAL_HOURS = 24   # hours between company-discovery passes
DISCOVERY_EVERY_N_PASSES = 8   # run discovery every N delta passes (≈ 24h at 3h cadence)

# Minimum characters for a page to be considered a real job description
MIN_JD_LENGTH = 350

# ── Domains to skip as crawl sources ──────────────────────────────────────────
SKIP_SOURCE_DOMAINS = {
    "linkedin.com", "naukri.com", "indeed.com", "glassdoor.com",
    "monster.com", "shine.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "youtube.com", "t.me", "telegram.org",
}

# ── Link patterns to never follow ─────────────────────────────────────────────
SKIP_LINK_RE = re.compile(
    r"(linkedin\.com|twitter\.com|x\.com|facebook\.com|instagram\.com|"
    r"youtube\.com|whatsapp|mailto:|tel:|javascript:|^#|google\.com/maps|"
    r"maps\.app|\.pdf$|\.zip$|\.png$|\.jpg$|\.jpeg$|\.gif$|"
    r"/logout|/login|/signin|/signup|/register|/privacy|/terms|"
    r"/cookie|/about$|/contact$|/blog/|/news/|/press/|/team$|"
    r"support\.|help\.|status\.)",
    re.IGNORECASE,
)

# ── Career / jobs page detection ──────────────────────────────────────────────
# Matches URLs that look like a company's careers landing page
CAREER_PAGE_RE = re.compile(
    r"/(career[s]?|job[s]?|join[- _]?us|work[- _]?with[- _]?us|"
    r"open[- _]position[s]?|opportunit[yi][es]?|hiring|vacancies?|"
    r"open[- _]role[s]?|we[- _]are[- _]hiring|life[- _]at|"
    r"come[- _]work|grow[- _]with[- _]us)(/|$|\?)",
    re.IGNORECASE,
)

# Anchor text that strongly suggests a "careers / jobs" navigation link
CAREER_ANCHOR_RE = re.compile(
    r"\b(career[s]?|jobs?|hiring|open\s+role[s]?|open\s+position[s]?|"
    r"vacancies|join\s+(us|our\s+team)|work\s+with\s+us|"
    r"life\s+at|we['']?re\s+hiring|opportunities)\b",
    re.IGNORECASE,
)

# ── Job-posting URL heuristics ─────────────────────────────────────────────────
JOB_URL_RE = re.compile(
    r"(/job[s]?/|/position[s]?/|/opening[s]?/|/vacancies?/|/apply/|"
    r"/requisition|/role[s]?/|/opportunit|"
    r"lever\.co/[^/]+/[^/]+|greenhouse\.io/|workday\.com/|"
    r"myworkdayjobs\.com/|smartrecruiters\.com/|breezy\.hr/|"
    r"ashbyhq\.com/|jobs\.ashby\.com/|apply\.workable\.com/|"
    r"taleo\.net/|icims\.com/|bamboohr\.com/careers/|"
    r"recruit\.zoho\.com/|instahyre\.com/job/|cutshort\.io/job|"
    r"wellfound\.com/jobs/|/careers/[a-z0-9_-]{4,}$)",
    re.IGNORECASE,
)

# Anchor text that hints a link points to an individual job posting
JOB_ANCHOR_RE = re.compile(
    r"\b(apply|view\s+job|see\s+role|view\s+role|open\s+position|"
    r"job\s+opening|learn\s+more|see\s+details|full\s+description|"
    r"performance\s+market|paid\s+media|digital\s+market|growth\s+market|"
    r"media\s+manager|media\s+lead|biddable|programmatic)\b",
    re.IGNORECASE,
)

# ══════════════════════════════════════════════════════════════════════════════
#  LAYER DEFINITIONS  — each returns True = SKIP
# ══════════════════════════════════════════════════════════════════════════════

# ── PRE-0 / PRE-1 : Empty / thin JD ──────────────────────────────────────────

# Structural job-page signals (at least one must be present after text extraction)
_JD_STRUCTURE_RE = re.compile(
    r"(responsibilit|requirement[s]?|qualif|experience|skill[s]?|"
    r"role|position|duties|what you.ll do|what we.re looking|"
    r"about the role|job description|key responsibilities|"
    r"you will be|we are looking)",
    re.IGNORECASE,
)

def _pre_empty(text: str) -> bool:
    """Return True (SKIP) if the page contains no usable job description."""
    if len(text) < MIN_JD_LENGTH:
        return True
    if not _JD_STRUCTURE_RE.search(text):
        return True
    return False


# ── L0 : Seniority gate (bi-directional) ─────────────────────────────────────
#
#  ACCEPT  →  Manager · Senior Manager · Lead · Senior · Sr.
#  SKIP    →  too-junior: Intern / Junior / Jr / Entry-Level / Fresher / Trainee / Graduate
#  SKIP    →  too-senior: Head · Director · VP · Vice President · Chief · Executive · President / C-level

_TOO_JUNIOR_RE = re.compile(
    r"\b(intern(ship)?|trainee|fresher|junior|jr\.?|"
    r"entry[- ]level|entry[- ]grade|graduate[- ]traineeship|"
    r"apprentice|0[- ]?[–-][- ]?[12]\s*years?|"
    r"less\s+than\s+[12]\s+years?)\b",
    re.IGNORECASE,
)

_TOO_SENIOR_RE = re.compile(
    r"\b(head\s+of|head,|vp\b|vice\s+president|chief\s+\w+\s+officer|"
    r"\bcmo\b|\bcco\b|\bceo\b|\bcto\b|executive\s+director|"
    r"president\s+of|\bpresident\b|global\s+head|country\s+head|"
    r"group\s+director|group\s+head|managing\s+director)\b",
    re.IGNORECASE,
)

def _l0_seniority(title: str, text_snippet: str = "") -> str | None:
    """
    Return skip reason string if seniority is out of range, else None.
    Checks both the job title and the first 500 chars of text.
    """
    combined = (title + " " + text_snippet[:500])
    if _TOO_JUNIOR_RE.search(title):          # title is the strongest signal
        return "too-junior title"
    if _TOO_SENIOR_RE.search(title):          # title is the strongest signal
        return "too-senior title"
    if _TOO_JUNIOR_RE.search(combined):
        return "too-junior content"
    return None


# ── L1a : PM semantic keyword cloud ──────────────────────────────────────────
#
# 9 clusters × rich synonyms.  A JD must hit ≥ MIN_PM_GROUPS distinct clusters
# AND ≥ MIN_PM_HITS total keyword matches to pass.
#
# Design philosophy (per user):
#   • Every brand uses slightly different terminology → broad synonyms per cluster
#   • Presence of a keyword alone isn't enough → cross-cluster density required
#   • "marketing" alone or "digital" alone must NOT pass the gate
_PM_GROUPS: list[re.Pattern] = [
    # ── Cluster 0 — Role / title identifiers (definitive PM signals) ──────────
    re.compile(
        r"(performance\s+marketing|paid\s+media\s+manag|paid\s+media\s+lead|"
        r"biddable\s+media|digital\s+performance|performance\s+lead|"
        r"growth\s+market|demand\s+gen(eration)?|user\s+acquisition|"
        r"performance\s+manag|media\s+buying\s+manag|media\s+buying\s+lead|"
        r"performance\s+campaigns|paid\s+performance|"
        r"acquisition\s+market|d2c\s+market|ecomm(erce)?\s+market)",
        re.IGNORECASE,
    ),
    # ── Cluster 1 — Paid Search / SEM / PPC ───────────────────────────────────
    re.compile(
        r"(\bppc\b|\bsem\b|paid\s+search|google\s+ads|\bgoogle\s+adwords\b|"
        r"search\s+ads|search\s+campaigns|keyword\s+bid|"
        r"shopping\s+ads|performance\s+max|p\s*max\b|"
        r"bing\s+ads|microsoft\s+ads|search\s+advertising)",
        re.IGNORECASE,
    ),
    # ── Cluster 2 — Paid Social ────────────────────────────────────────────────
    re.compile(
        r"(meta\s+ads|facebook\s+ads|instagram\s+ads|paid\s+social|"
        r"social\s+advert|tiktok\s+ads|snapchat\s+ads|pinterest\s+ads|"
        r"twitter\s+ads|x\s+ads|linkedin\s+ads|social\s+media\s+buying|"
        r"social\s+media\s+advert)",
        re.IGNORECASE,
    ),
    # ── Cluster 3 — Programmatic / Display / Video ────────────────────────────
    re.compile(
        r"(\bdv360\b|display\s+advert|display\s+campaign|"
        r"campaign\s+manager\s+360|\bcm360\b|\bdsp\b|\brtb\b|"
        r"the\s+trade\s+desk|\bttd\b|programmatic\s+buying|"
        r"programmatic\s+advert|youtube\s+ads|connected\s+tv|\bctv\b|"
        r"video\s+campaign|ott\s+advert)",
        re.IGNORECASE,
    ),
    # ── Cluster 4 — Performance KPIs / Metrics ────────────────────────────────
    re.compile(
        r"(\broas\b|\bcpa\b|\bcpl\b|\bcpc\b|\bctr\b|\bcpm\b|\broi\b|"
        r"return\s+on\s+ad\s+spend|cost\s+per\s+acqui|cost\s+per\s+lead|"
        r"cost\s+per\s+click|conversion\s+rate|funnel\s+optimis|"
        r"media\s+efficiency|\bacos\b|blended\s+roas|northstar\s+metric|"
        r"marketing\s+efficiency|mer\b)",
        re.IGNORECASE,
    ),
    # ── Cluster 5 — Analytics / Attribution / Tracking ───────────────────────
    re.compile(
        r"(google\s+analytics|\bga4\b|data\s+studio|looker\s+studio|"
        r"\battribution\b|a/?b\s+test|split\s+test|\bpixel\b|"
        r"tracking\s+setup|utm\s+param|appsflyer|adjust\b|branch\b|"
        r"moengage|clevertap|amplitude\b|mixpanel|"
        r"incrementality|media\s+mix\s+model|\bmmm\b)",
        re.IGNORECASE,
    ),
    # ── Cluster 6 — Budget / Planning / Strategy ──────────────────────────────
    re.compile(
        r"(media\s+plan|budget\s+allocat|campaign\s+plan|ad\s+spend|"
        r"monthly\s+budget|annual\s+budget|media\s+budget|"
        r"media\s+strategy|channel\s+mix|media\s+mix|"
        r"forecasting|pacing|spend\s+optimis|bid\s+strateg)",
        re.IGNORECASE,
    ),
    # ── Cluster 7 — Agency / Vendor management ────────────────────────────────
    re.compile(
        r"(agency\s+manag|vendor\s+manag|media\s+agency|digital\s+agency|"
        r"google\s+partner|meta\s+partner|platform\s+partner|"
        r"creative\s+testing|ad\s+creative|creative\s+optimis|"
        r"campaign\s+execut|campaign\s+manag|campaign\s+optimis)",
        re.IGNORECASE,
    ),
    # ── Cluster 8 — Growth / Funnel / CRM ────────────────────────────────────
    re.compile(
        r"(full[- ]funnel|tofu\b|mofu\b|bofu\b|retargeting|remarketing|"
        r"audience\s+segm|lookalike\s+audience|customer\s+acqui|"
        r"retention\s+market|lifecycle\s+market|crm\s+integrat|"
        r"lead\s+gen(eration)?|inbound\s+market|growth\s+hack)",
        re.IGNORECASE,
    ),
]
MIN_PM_GROUPS = 2
MIN_PM_HITS   = 3

def _l1a_pm_density(text: str) -> bool:
    """Return True (SKIP) if PM keyword density is too low."""
    groups_hit = 0
    total_hits = 0
    for pat in _PM_GROUPS:
        hits = len(pat.findall(text))
        if hits:
            groups_hit += 1
            total_hits += hits
    return groups_hit < MIN_PM_GROUPS or total_hits < MIN_PM_HITS


# ── L1b : User keyword cross-check ───────────────────────────────────────────

def _l1b_user_keywords(text: str, user_keywords: list[str]) -> bool:
    """
    Return True (SKIP) if NONE of the user's search_keywords appear in the JD.
    Case-insensitive substring match.
    """
    if not user_keywords:
        return False  # no filter configured
    text_lower = text.lower()
    for kw in user_keywords:
        if kw.strip().lower() in text_lower:
            return False
    return True  # none matched → skip


# ── L2 : Location hard gate ───────────────────────────────────────────────────

_REMOTE_RE = re.compile(
    r"\b(remote|hybrid|work\s+from\s+home|wfh|flexible\s+location|"
    r"location\s+agnostic|open\s+to\s+remote|pan[- ]india)\b",
    re.IGNORECASE,
)

def _l2_location(text: str, city: str) -> bool:
    """
    Return True (SKIP) if target city is absent AND it's not remote/hybrid.
    City must appear explicitly in JD text — no exceptions.
    """
    if not city:
        return False
    if re.search(re.escape(city), text, re.IGNORECASE):
        return False
    if _REMOTE_RE.search(text):
        return False
    return True  # SKIP — wrong region


# ── L3 : Role title function check ───────────────────────────────────────────

_NON_PM_FUNCTION_RE = re.compile(
    r"\b(software\s+engineer|backend|frontend|full[- ]?stack|devops|"
    r"data\s+engineer|data\s+analyst|data\s+scientist|"
    r"product\s+manager|product\s+owner|program\s+manager|"
    r"finance\s+manager|accounting|accountant|"
    r"hr\s+manager|human\s+resources|recruiter|talent\s+acquisition|"
    r"graphic\s+design(er)?|content\s+writer|copywriter|"
    r"seo\s+specialist|social\s+media\s+manager|community\s+manager|"
    r"ui\s+(designer|developer)|ux\s+designer|video\s+editor|"
    r"business\s+development|sales\s+manager|account\s+manager|"
    r"customer\s+success|operations\s+manager|project\s+manager)\b",
    re.IGNORECASE,
)

def _l3_role_title(title: str) -> bool:
    """Return True (SKIP) if title is clearly a non-PM job function."""
    if not title:
        return False
    return bool(_NON_PM_FUNCTION_RE.search(title))


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def _extract_title(soup: BeautifulSoup) -> str:
    for sel in (
        "h1.job-title", "h1[class*='title']", "h1[class*='job']",
        "h1[class*='position']", "h1[class*='role']",
        "[class*='jobTitle']", "[class*='job-title']",
        "[class*='position-title']", "h1",
    ):
        tag = soup.select_one(sel)
        if tag:
            txt = tag.get_text(separator=" ", strip=True)
            if 3 < len(txt) < 130:
                return txt
    if (title_tag := soup.find("title")):
        raw = title_tag.get_text(" ", strip=True)
        return re.split(r"[|–—]", raw)[0].strip()[:130]
    return ""


def _fetch_page(url: str) -> tuple[str, Optional[BeautifulSoup]]:
    """Fetch page, strip boilerplate, return (plain_text, soup)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return "", None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "meta"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True), soup
    except Exception:
        return "", None


# ══════════════════════════════════════════════════════════════════════════════
#  GOOGLE SEARCH  (HTML scraping — no API key required)
# ══════════════════════════════════════════════════════════════════════════════

_GOOGLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer":         "https://www.google.com/",
}

def _google_search(query: str, num: int = 10) -> list[str]:
    """
    Scrape Google Search result URLs for `query`.
    Returns a list of landing-page URLs (Google redirects stripped).
    """
    urls: list[str] = []
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": query, "num": num, "hl": "en", "gl": "in"},
            headers=_GOOGLE_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.debug(f"[Google] HTTP {resp.status_code} for: {query}")
            return urls
        soup = BeautifulSoup(resp.text, "html.parser")
        # Google wraps organic result links as <a href="/url?q=https://...&...">
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/url?q="):
                real = unquote(href[7:].split("&")[0])
                if (
                    real.startswith("http")
                    and "google.com" not in real
                    and "googleusercontent" not in real
                    and not SKIP_LINK_RE.search(real)
                ):
                    urls.append(real)
    except Exception as e:
        logger.debug(f"[Google] Search failed for '{query}': {e}")
    return list(dict.fromkeys(urls))  # deduplicate while preserving order


def _build_google_queries(city: str, keywords: list[str]) -> list[str]:
    """
    Compose targeted Google search queries for Performance Marketing roles.
    Includes site-specific searches for high-quality ATS platforms and
    job boards that don't block crawlers.
    """
    c   = city or "Mumbai"
    kw0 = keywords[0] if keywords else "Performance Marketing"
    # Build "any keyword" OR string (max 4 to stay under Google query length)
    kw_or = " OR ".join(f'"{k}"' for k in keywords[:4]) if keywords else f'"{kw0}"'

    return [
        # Direct ATS / career-page job searches
        f'site:lever.co ({kw_or}) "{c}"',
        f'site:greenhouse.io ({kw_or}) "{c}"',
        f'site:ashbyhq.com ({kw_or}) "{c}"',
        f'site:workable.com ({kw_or}) "{c}"',
        f'site:bamboohr.com ({kw_or}) "{c}"',
        f'site:smartrecruiters.com ({kw_or}) "{c}"',
        # Indian job boards with public search pages
        f'site:instahyre.com {kw0} {c}',
        f'site:cutshort.io {kw0} {c}',
        f'site:wellfound.com {kw0} {c}',
        f'site:foundit.in {kw0} {c}',
        f'site:iimjobs.com {kw0} {c}',
        # Broad Google search for recent job pages
        f'"{c}" ({kw_or}) job apply 2025 -site:linkedin.com -site:naukri.com',
        f'"{c}" "performance marketing manager" OR "media lead" job hiring -site:linkedin.com',
        f'"{c}" "paid media" manager lead opening apply now',
    ]


def _dynamic_job_urls(city: str, keywords: list[str]) -> list[str]:
    """Run all Google queries and return deduplicated candidate job URLs."""
    queries = _build_google_queries(city, keywords)
    all_urls: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for url in _google_search(q):
            if url not in seen:
                seen.add(url)
                all_urls.append(url)
        time.sleep(2.0)  # polite delay between Google requests
    logger.info(f"[Scanner] Google search found {len(all_urls)} candidate URLs")
    return all_urls


# ══════════════════════════════════════════════════════════════════════════════
#  STATS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ScanStats:
    sources_checked:   int = 0
    links_found:       int = 0
    # Pre-filters (free)
    skipped_empty:     int = 0
    skipped_junior:    int = 0
    skipped_senior:    int = 0
    skipped_no_pm:     int = 0
    skipped_no_kw:     int = 0
    skipped_no_loc:    int = 0
    skipped_wrong_fn:  int = 0
    skipped_duplicate: int = 0
    # AI filters (paid)
    jobs_sent_to_ai:   int = 0
    jobs_ai_filtered:  int = 0
    jobs_saved:        int = 0
    jobs_queued:       int = 0
    errors:              int = 0
    companies_discovered: int = 0   # new companies found by the discovery pass
    current_source:      str = ""
    status:              str = "idle"
    last_run_start:      Optional[str] = None
    last_run_end:        Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
#  JOB SCANNER
# ══════════════════════════════════════════════════════════════════════════════

class JobScanner:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock    = threading.Lock()
        self._app     = None
        self.stats    = ScanStats()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self, app) -> dict:
        with self._lock:
            if self._running:
                return {"started": False, "reason": "already_running"}
            self._running = True
            self._app     = app
            self.stats    = ScanStats(status="running")
            self._thread  = threading.Thread(
                target=self._continuous_loop, daemon=True, name="job_scanner"
            )
            self._thread.start()
        logger.info("[Scanner] Continuous mode started.")
        return {"started": True, "mode": "continuous"}

    def stop(self) -> dict:
        with self._lock:
            if not self._running:
                return {"stopped": False, "reason": "not_running"}
            self._running = False
            self.stats.status = "idle"
            self.stats.current_source = ""
        logger.info("[Scanner] Stop requested.")
        return {"stopped": True}

    def run_once(self, app) -> dict:
        with self._lock:
            if self._running:
                return {"started": False, "reason": "already_running"}
            self._running = True
            self._app     = app
            self.stats    = ScanStats(status="scanning")

        def _once():
            try:
                self._scan_pass()
            finally:
                with self._lock:
                    self._running = False
                    self.stats.status = "idle"
                    self.stats.current_source = ""

        threading.Thread(target=_once, daemon=True, name="scanner_once").start()
        return {"started": True, "mode": "single_pass"}

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        s = self.stats
        pre_filtered = (
            s.skipped_empty + s.skipped_junior + s.skipped_senior
            + s.skipped_no_pm + s.skipped_no_kw + s.skipped_no_loc
            + s.skipped_wrong_fn + s.skipped_duplicate
        )
        return {
            "is_running":     self._running,
            "status":         s.status,
            "current_source": s.current_source,
            "last_run_start": s.last_run_start,
            "last_run_end":   s.last_run_end,
            "stats": {
                "sources_checked": s.sources_checked,
                "links_found":     s.links_found,
                "jobs_saved":      s.jobs_saved,
                "jobs_queued":     s.jobs_queued,
                "jobs_sent_to_ai": s.jobs_sent_to_ai,
                "ai_filtered":     s.jobs_ai_filtered,
                "pre_filtered":    pre_filtered,
                "errors":               s.errors,
                "companies_discovered": s.companies_discovered,
                "filters": {
                    "empty_jd":    s.skipped_empty,
                    "too_junior":  s.skipped_junior,
                    "too_senior":  s.skipped_senior,
                    "no_pm_kw":    s.skipped_no_pm,
                    "no_user_kw":  s.skipped_no_kw,
                    "no_location": s.skipped_no_loc,
                    "wrong_fn":    s.skipped_wrong_fn,
                    "duplicate":   s.skipped_duplicate,
                    "ai_rejected": s.jobs_ai_filtered,
                },
            },
        }

    # ── Settings ───────────────────────────────────────────────────────────────

    @staticmethod
    def load_settings() -> dict:
        try:
            with open(SETTINGS_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_settings(data: dict) -> None:
        current: dict = {}
        try:
            with open(SETTINGS_PATH) as f:
                current = json.load(f)
        except Exception:
            pass
        current.update(data)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(current, f, indent=2)

    # ── Main loops ─────────────────────────────────────────────────────────────

    def _continuous_loop(self):
        pass_count = 0
        while self._running:
            # Always run the delta pass (fast check of existing companies)
            self._scan_pass(mode="delta")

            # Every DISCOVERY_EVERY_N_PASSES delta passes also run discovery
            if pass_count % DISCOVERY_EVERY_N_PASSES == 0:
                self._discovery_pass()

            pass_count += 1
            self.stats.status = "waiting"
            slept = 0
            while slept < SCAN_INTERVAL_HOURS * 3600 and self._running:
                time.sleep(10)
                slept += 10
        self.stats.status = "idle"

    def _scan_pass(self, mode: str = "full"):
        self.stats.last_run_start = datetime.now().isoformat()
        self.stats.status         = "scanning"

        settings = self.load_settings()
        city     = settings.get("location", "Mumbai").strip()
        keywords = settings.get("search_keywords", ["Performance Marketing"])
        auto_start = settings.get("auto_start_agent", True)

        logger.info(f"[Scanner] Pass starting — city='{city}' keywords={keywords[:3]}")

        # ── 1. Static company career pages ────────────────────────────────────
        static = self._build_static_sources(city, keywords)
        for src in static:
            if not self._running:
                break
            try:
                self._crawl_source(src, settings)
            except Exception as e:
                logger.warning(f"[Scanner] Source error {src['name']}: {e}")
                self.stats.errors += 1
            time.sleep(DELAY_BETWEEN_PAGES)

        # ── 2. Dynamic Google-search URLs ─────────────────────────────────────
        google_urls = _dynamic_job_urls(city, keywords)
        for job_url in google_urls:
            if not self._running:
                break
            try:
                self._run_pipeline(job_url, settings)
            except Exception as e:
                logger.debug(f"[Scanner] Pipeline error {job_url}: {e}")
                self.stats.errors += 1
            time.sleep(DELAY_BETWEEN_PAGES)

        self.stats.last_run_end   = datetime.now().isoformat()
        self.stats.current_source = ""

        # Auto-start agent when new A/B jobs queued
        if auto_start and self.stats.jobs_queued > 0 and self._app:
            try:
                from services.agent import career_agent
                if not career_agent.is_running:
                    career_agent.start(self._app)
                    logger.info("[Scanner] Auto-started agent.")
            except Exception as e:
                logger.warning(f"[Scanner] Auto-start agent error: {e}")

        s = self.stats
        pre = (s.skipped_empty + s.skipped_junior + s.skipped_senior
               + s.skipped_no_pm + s.skipped_no_kw + s.skipped_no_loc
               + s.skipped_wrong_fn + s.skipped_duplicate)
        logger.info(
            f"[Scanner] Pass complete — "
            f"links:{s.links_found} pre-filtered:{pre} "
            f"ai-sent:{s.jobs_sent_to_ai} saved:{s.jobs_saved} queued:{s.jobs_queued}"
        )

    # ── Company discovery pass ─────────────────────────────────────────────────

    def _discovery_pass(self):
        """
        Runs on a 24-hour cadence.  Uses Google searches to find companies
        that are currently posting Performance Marketing roles but are NOT
        yet tracked in our database.

        Flow:
          1. Build discovery-specific Google queries
          2. For each result URL → extract company domain + career page
          3. If the domain is not in CompanySource DB → add it as a new source
          4. Do an immediate first-scan crawl on each new company
          5. Also sync new entries back into job_sources.json for static scanner
        """
        if not self._app:
            return

        settings = self.load_settings()
        city     = settings.get("location", "Mumbai").strip()
        keywords = settings.get("search_keywords", ["Performance Marketing"])

        self.stats.status = "discovering"
        logger.info("[Discovery] Starting company discovery pass")

        queries = self._build_discovery_queries(city, keywords)
        seen_domains: set[str] = set()
        new_companies: list[dict] = []

        # ── Load already-known domains ─────────────────────────────────────────
        with self._app.app_context():
            from models.database import CompanySource
            known_domains = {
                urlparse(r.source_url).netloc.lower().lstrip("www.")
                for r in CompanySource.query.all()
            }

        # Also include static sources from job_sources.json
        try:
            with open(SOURCES_PATH) as f:
                static_data = json.load(f)
            for section in static_data.values():
                items = section if isinstance(section, list) else []
                for item in items:
                    if isinstance(item, dict):
                        u = item.get("careers") or item.get("url", "")
                        if u:
                            d = urlparse(u).netloc.lower().lstrip("www.")
                            known_domains.add(d)
        except Exception:
            pass

        # ── Run discovery queries ──────────────────────────────────────────────
        for query in queries:
            if not self._running:
                break
            urls = _google_search(query, num=10)
            for result_url in urls:
                if not self._running:
                    break
                domain = urlparse(result_url).netloc.lower().lstrip("www.")

                # Skip job portals and known domains
                if any(d in domain for d in SKIP_SOURCE_DOMAINS):
                    continue
                if domain in known_domains or domain in seen_domains:
                    continue

                seen_domains.add(domain)

                # Extract a clean company name from the domain
                company_name = self._name_from_domain(domain)

                # Attempt to find career page for this new company
                base_url = f"https://{domain}"
                careers_url = self._find_careers_page(base_url) or base_url

                logger.info(f"[Discovery] New company found: {company_name} — {careers_url}")

                new_companies.append({
                    "name":        company_name,
                    "source_url":  base_url,
                    "careers_url": careers_url,
                })
            time.sleep(2.5)  # polite delay

        if not new_companies:
            logger.info("[Discovery] No new companies found this pass")
            return

        # ── Persist new companies to DB + JSON ────────────────────────────────
        with self._app.app_context():
            from models.database import db, CompanySource
            added = 0
            for nc in new_companies:
                try:
                    row = CompanySource.upsert(nc["name"], nc["source_url"])
                    if nc["careers_url"] != nc["source_url"]:
                        row.careers_url = nc["careers_url"]
                    db.session.commit()
                    added += 1
                    known_domains.add(urlparse(nc["source_url"]).netloc.lower().lstrip("www."))
                except Exception as e:
                    logger.warning(f"[Discovery] DB insert error for {nc['name']}: {e}")
                    db.session.rollback()

        self.stats.companies_discovered += len(new_companies)
        logger.info(f"[Discovery] Added {len(new_companies)} new compan(ies) to watchlist")

        # Sync back to job_sources.json so next static build includes them
        self._sync_db_companies_to_json()

        # ── Immediately do a first-scan crawl on each new company ─────────────
        for nc in new_companies:
            if not self._running:
                break
            src = {
                "name": nc["name"],
                "url":  nc["careers_url"],
                "type": "career_page",
            }
            try:
                self._crawl_source(src, settings)
            except Exception as e:
                logger.warning(f"[Discovery] First-scan error {nc['name']}: {e}")
            time.sleep(DELAY_BETWEEN_PAGES)

        logger.info("[Discovery] Company discovery pass complete")

    def _build_discovery_queries(self, city: str, keywords: list[str]) -> list[str]:
        """
        Queries designed to surface company career pages and ATS postings
        for PM roles — NOT job portal listings.
        Covers a mix of:
          • City-specific postings on known ATS platforms
          • Broad searches for companies currently hiring PM profiles
          • Sector-specific searches (D2C, agency, SaaS, FMCG, fintech)
        """
        c   = city or "Mumbai"
        kw0 = keywords[0] if keywords else "Performance Marketing"
        kw_or = " OR ".join(f'"{k}"' for k in keywords[:4]) if keywords else f'"{kw0}"'

        return [
            # ATS platform searches (each result = a company hiring PM)
            f'site:lever.co ({kw_or}) "{c}"',
            f'site:greenhouse.io ({kw_or}) "{c}"',
            f'site:ashbyhq.com ({kw_or}) "{c}"',
            f'site:workable.com ({kw_or}) "{c}"',
            f'site:bamboohr.com/jobs ({kw_or}) "{c}"',
            f'site:smartrecruiters.com ({kw_or}) "{c}"',
            # Indian job boards with public access
            f'site:cutshort.io {kw0} {c}',
            f'site:wellfound.com {kw0} {c}',
            f'site:instahyre.com {kw0} {c}',
            # Sector-specific company discovery
            f'D2C brand "performance marketing" hiring {c} -site:linkedin.com',
            f'digital agency "paid media" OR "biddable" hiring {c} -site:naukri.com',
            f'e-commerce startup "growth marketing" OR "user acquisition" {c} apply',
            f'FMCG "performance marketing manager" job {c} apply -site:linkedin.com',
            f'fintech "paid media" OR "growth marketing" job {c} hiring',
            f'SaaS startup "performance marketing" India hiring apply',
            # Broad discovery queries
            f'"{c}" ({kw_or}) hiring "apply now" -site:linkedin.com -site:naukri.com',
            f'({kw_or}) "we are hiring" OR "join our team" {c} -site:linkedin.com',
        ]

    @staticmethod
    def _name_from_domain(domain: str) -> str:
        """Extract a readable company name from a domain."""
        # Strip TLD and subdomains: e.g. "careers.mamaearth.in" → "Mamaearth"
        parts = domain.split(".")
        # Take the last meaningful word before the TLD
        candidates = [p for p in parts if p not in ("www", "careers", "jobs", "in", "com", "co", "io")]
        name = candidates[0] if candidates else domain
        return name.replace("-", " ").replace("_", " ").title()

    def _sync_db_companies_to_json(self) -> None:
        """Merge DB company list into job_sources.json user_companies array."""
        try:
            if not self._app:
                return
            with self._app.app_context():
                from models.database import CompanySource
                db_list = CompanySource.all_as_list()

            try:
                with open(SOURCES_PATH) as f:
                    sources = json.load(f)
            except Exception:
                sources = {}

            existing = {c.get("careers", "") for c in sources.get("user_companies", [])}
            new_entries = [c for c in db_list if c["careers"] not in existing]
            sources.setdefault("user_companies", []).extend(new_entries)

            with open(SOURCES_PATH, "w") as f:
                json.dump(sources, f, indent=2)

            if new_entries:
                logger.debug(f"[Discovery] Synced {len(new_entries)} new companies to job_sources.json")
        except Exception as e:
            logger.warning(f"[Discovery] JSON sync error: {e}")

    # ── Scan memory ────────────────────────────────────────────────────────────

    def _load_scan_memory(self) -> dict:
        """Load per-source scan history from disk."""
        try:
            with open(SCAN_MEMORY_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_scan_memory(self, memory: dict) -> None:
        try:
            with open(SCAN_MEMORY_PATH, "w") as f:
                json.dump(memory, f, indent=2)
        except Exception as e:
            logger.warning(f"[Scanner] Could not save scan memory: {e}")

    def _source_sort_key(self, url: str, memory: dict) -> tuple:
        """Return a sort tuple so sources are scanned in the right order.

        Priority within each tier (lower = sooner):
          0 — never seen              (always scan first)
          1 — found jobs last time    (high-value, always rescan early)
          2 — no jobs, cooldown done  (scan in normal order)
          3 — no jobs, still cooling  (scan last; don't skip entirely)
        """
        rec = memory.get(url)
        if rec is None:
            return (0,)
        jobs = rec.get("jobs_found_last", 0)
        if jobs > 0:
            return (1,)
        last = rec.get("last_scanned", "")
        if last:
            try:
                dt = datetime.fromisoformat(last)
                hours_ago = (datetime.now() - dt).total_seconds() / 3600
                if hours_ago < SOURCE_COOLDOWN_HOURS:
                    return (3,)
            except Exception:
                pass
        return (2,)

    def _update_source_memory(
        self, url: str, jobs_found: int, job_url_snapshot: set | None = None
    ) -> None:
        """
        Record that a source was just scanned.
        Optionally persist the full set of job-link URLs seen this pass
        so the next pass can diff and only process genuinely new ones.
        """
        mem  = self._load_scan_memory()
        prev = mem.get(url, {})
        entry: dict = {
            "last_scanned":    datetime.now().isoformat(),
            "jobs_found_last": jobs_found,
            "scan_count":      prev.get("scan_count", 0) + 1,
        }
        if job_url_snapshot is not None:
            # Merge with existing snapshot so URLs removed from the page
            # don't cause false "new" signals if they reappear later.
            old_set = set(prev.get("known_urls", []))
            new_set = old_set | job_url_snapshot
            entry["known_urls"] = sorted(new_set)
        elif "known_urls" in prev:
            entry["known_urls"] = prev["known_urls"]   # preserve across passes

        mem[url] = entry
        self._save_scan_memory(mem)

    def _get_known_urls(self, url: str) -> set:
        """Return the snapshot of job-link URLs seen at a source on the last crawl."""
        mem = self._load_scan_memory()
        return set(mem.get(url, {}).get("known_urls", []))

    # ── Static source list ─────────────────────────────────────────────────────

    def _build_static_sources(self, city: str, keywords: list) -> list:
        """Build source list ordered by thinking priority + scan memory.

        Scan order (thinking capability):
          Tier 0 — user-provided companies (highest priority)
          Tier 1 — marketing / digital agencies (most likely to hire PMs)
          Tier 2 — D2C brands, e-commerce, quick-commerce
          Tier 3 — tech platforms, FMCG, B2B SaaS, fintech, edtech
          Tier 4 — consulting, travel, UAE conglomerates
          Tier 5 — VC portfolios, job portals, extra search URLs

        Within each tier:
          Rank 0 — never scanned (always first)
          Rank 1 — found jobs last time (high-value, rescan early)
          Rank 2 — no jobs but cooldown expired
          Rank 3 — no jobs, still in {SOURCE_COOLDOWN_HOURS}h cooldown (scan last)
        """
        try:
            with open(SOURCES_PATH) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[Scanner] Cannot load sources: {e}")
            return []

        memory  = self._load_scan_memory()
        query   = keywords[0] if keywords else "Performance Marketing"
        tiered: list[tuple] = []   # (tier, rank_tuple, url, source_dict)
        seen:   set[str] = set()

        def _norm(url: str) -> str:
            if not url:
                return ""
            if not url.startswith("http"):
                url = "https://" + url
            return url

        def _add(name: str, url: str, stype: str, tier: int = 5) -> None:
            url = _norm(url)
            if not url:
                return
            if any(d in url for d in SKIP_SOURCE_DOMAINS):
                return
            if url in seen:
                return
            seen.add(url)
            rank = self._source_sort_key(url, memory)
            tiered.append((tier, rank, url, {"name": name, "url": url, "type": stype}))

        def _flat(raw) -> list:
            """Flatten list-or-region-dict to a flat item list."""
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict):
                return [item for vlist in raw.values()
                        for item in (vlist if isinstance(vlist, list) else [])]
            return []

        # ── Tier 0: user-provided company list ───────────────────────────────
        for item in data.get("user_companies", []):
            if isinstance(item, dict):
                c = item.get("careers") or item.get("url", "")
                _add(item.get("name", c), c, "career_page", tier=0)

        # ── Tier 1: marketing / digital agencies ─────────────────────────────
        for top_key in ("agency_networks", "boutique_agencies"):
            for item in _flat(data.get(top_key, {})):
                if isinstance(item, dict):
                    c = item.get("careers") or item.get("url", "")
                    _add(item.get("name", c), c, "career_page", tier=1)

        # ── Tier 2: D2C brands, e-commerce, quick-commerce ───────────────────
        for key in ("d2c_brands", "ecommerce", "quick_commerce"):
            for item in _flat(data.get(key, [])):
                if isinstance(item, dict):
                    c = item.get("careers") or item.get("url", "")
                    _add(item.get("name", c), c, "career_page", tier=2)

        # ── Tier 3: tech platforms, FMCG, SaaS, fintech, edtech ─────────────
        for key in ("tech_platforms", "fmcg_cpg", "b2b_saas", "edtech"):
            for item in _flat(data.get(key, [])):
                if isinstance(item, dict):
                    c = item.get("careers") or item.get("url", "")
                    _add(item.get("name", c), c, "career_page", tier=3)
        for item in _flat(data.get("fintech", [])):
            if isinstance(item, dict):
                c = item.get("careers") or item.get("url", "")
                _add(item.get("name", c), c, "career_page", tier=3)

        # ── Tier 4: consulting, travel, UAE conglomerates ────────────────────
        for key in ("consulting", "travel_hospitality", "uae_conglomerates"):
            for item in _flat(data.get(key, [])):
                if isinstance(item, dict):
                    c = item.get("careers") or item.get("url", "")
                    _add(item.get("name", c), c, "career_page", tier=4)

        # ── Tier 5: VC portfolio, job portals, extra search URLs ─────────────
        for item in data.get("vc_portfolio_jobs", []):
            if isinstance(item, dict):
                u = item.get("url", "")
                _add(item.get("name", u), u, "career_page", tier=5)

        loc_q = quote(city or "India")
        q_enc = quote(query)
        for region, portal_list in data.get("job_portals", {}).items():
            for portal in (portal_list if isinstance(portal_list, list) else []):
                if not isinstance(portal, dict):
                    continue
                tpl  = portal.get("search_url", "")
                base = portal.get("url", "")
                if not tpl or any(d in base for d in SKIP_SOURCE_DOMAINS):
                    continue
                url = tpl.replace("{query}", q_enc).replace("{location}", loc_q)
                _add(portal.get("name", base), url, "search_results", tier=5)

        for name, url in self._extra_pm_sources(city, query):
            _add(name, url, "search_results", tier=5)

        # Sort: tier ASC, then rank tuple (never-seen → found-jobs → expired → cooldown)
        tiered.sort(key=lambda x: (x[0], x[1]))

        counts = {}
        for t, _, _, _ in tiered:
            counts[t] = counts.get(t, 0) + 1
        logger.info(
            f"[Scanner] Source plan: "
            + " | ".join(
                f"T{t}={n}" for t, n in sorted(counts.items())
            )
        )

        return [x[3] for x in tiered]

    def _extra_pm_sources(self, city: str, keyword: str) -> list[tuple[str, str]]:
        c  = quote(city or "Mumbai")
        kw = quote(keyword or "Performance Marketing")
        return [
            ("Instahyre PM Jobs",    f"https://www.instahyre.com/search-jobs/?query={kw}&location={c}"),
            ("Cutshort PM Jobs",     f"https://cutshort.io/jobs?q={kw}&location={c}"),
            ("Wellfound PM Jobs",    f"https://wellfound.com/jobs?role=performance-marketing&location={c}"),
            ("Foundit PM Jobs",      f"https://www.foundit.in/srp/results?query={kw}&location={c}"),
            ("Hirist PM Jobs",       f"https://www.hirist.tech/search-jobs?q={kw}&l={c}"),
            ("iimjobs PM",           f"https://www.iimjobs.com/search.php?q={kw}&loc={c}"),
            ("Lever Public Board",   f"https://jobs.lever.co/?search={kw}"),
        ]

    # ── Career page discovery ──────────────────────────────────────────────────

    def _find_careers_page(self, base_url: str) -> str | None:
        """
        Given a company's main or arbitrary URL, try to find their actual
        careers / jobs page.

        Strategy (in order):
          1. If the URL already looks like a careers/jobs page → use it as-is.
          2. Try common path suffixes (/careers, /jobs, /join-us, etc.).
          3. Fetch the homepage and look for career-related navigation links.

        Returns the best candidate URL or None.
        """
        parsed   = urlparse(base_url)
        base_dom = parsed.netloc
        origin   = f"{parsed.scheme}://{base_dom}"

        # ── Step 1: already a careers page? ───────────────────────────────────
        if CAREER_PAGE_RE.search(parsed.path):
            return base_url

        # ── Step 2: try standard paths ────────────────────────────────────────
        common_paths = [
            "/careers", "/jobs", "/join-us", "/work-with-us",
            "/careers/", "/jobs/", "/open-roles", "/openings",
            "/opportunities", "/hiring", "/life-at",
        ]
        for path in common_paths:
            candidate = origin + path
            try:
                r = requests.head(
                    candidate, headers=HEADERS,
                    timeout=8, allow_redirects=True
                )
                if r.status_code == 200:
                    logger.debug(f"[Discover] Career page found via HEAD: {candidate}")
                    return candidate
            except Exception:
                continue

        # ── Step 3: scrape homepage for career nav links ───────────────────────
        try:
            r = requests.get(base_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.text, "html.parser")
            best: str | None = None
            for tag in soup.find_all("a", href=True):
                href   = tag["href"].strip()
                abs_u  = urljoin(base_url, href)
                anchor = tag.get_text(separator=" ", strip=True)
                if SKIP_LINK_RE.search(abs_u):
                    continue
                # Prioritise URL-based match over anchor-text match
                if CAREER_PAGE_RE.search(urlparse(abs_u).path):
                    return abs_u
                if best is None and CAREER_ANCHOR_RE.search(anchor):
                    best = abs_u
            return best
        except Exception:
            return None

    def _google_company_jobs(
        self, name: str, domain: str, keywords: list[str]
    ) -> list[str]:
        """
        Google fallback: when a company has no crawlable career page,
        search Google for recent job postings from that brand.

        Searches:
          1. site:[domain] careers OR jobs OR hiring
          2. "[name]" performance marketing jobs Mumbai apply
        """
        kw0 = keywords[0] if keywords else "Performance Marketing"
        kw_or = " OR ".join(f'"{k}"' for k in keywords[:3])
        results: list[str] = []

        q1 = f'site:{domain} (careers OR jobs OR hiring OR "open roles")'
        q2 = f'"{name}" ({kw_or}) job apply -site:linkedin.com -site:naukri.com'

        for q in (q1, q2):
            urls = _google_search(q, num=6)
            for u in urls:
                if u not in results:
                    results.append(u)
            time.sleep(2.0)

        logger.debug(f"[GoogleFallback] {name}: {len(results)} URLs via Google")
        return results[:10]

    # ── Career page crawl ──────────────────────────────────────────────────────

    def _crawl_source(self, source: dict, settings: dict):
        """
        Smart career page crawler.  For each source:

          Phase A — Discover the careers page
            • If the URL already has /careers /jobs / etc. → use it directly
            • Try common path suffixes (HEAD requests, fast)
            • Scrape homepage looking for career nav links

          Phase B — Extract job links from the careers page
            • ATS platform patterns (Lever, Greenhouse, Workday …)
            • Same-domain links with job-like URL segments
            • Any link whose anchor text contains job-related keywords

          Phase C — Google fallback
            • If Phase B yields 0 candidates, search Google for the company
              name + performance marketing keywords to find postings elsewhere

          Phase D — Evaluate each candidate through the 7-layer pipeline
        """
        name        = source["name"]
        url         = source["url"]
        stype       = source.get("type", "career_page")
        jobs_before = self.stats.jobs_saved
        self.stats.current_source = name
        self.stats.sources_checked += 1

        # ── Phase A: Find the real careers page (skip for portal search results)
        careers_url = url
        if stype != "search_results":
            discovered = self._find_careers_page(url)
            if discovered:
                careers_url = discovered
                if discovered != url:
                    logger.debug(f"[Discover] {name}: {url} → {discovered}")
            else:
                logger.debug(f"[Discover] {name}: no career page found at {url}")

        # ── Phase B: Extract job link candidates from careers page ─────────────
        candidates: list[str] = []

        try:
            resp = requests.get(
                careers_url, headers=HEADERS,
                timeout=REQUEST_TIMEOUT, allow_redirects=True
            )
            if resp.status_code != 200:
                resp = None
        except Exception as e:
            logger.debug(f"[Scanner] Fetch {careers_url}: {e}")
            resp = None

        if resp:
            soup     = BeautifulSoup(resp.text, "html.parser")
            base_dom = urlparse(careers_url).netloc
            seen_cands: set[str] = set()

            for tag in soup.find_all("a", href=True):
                href        = tag["href"].strip()
                abs_url     = urljoin(careers_url, href)
                anchor_text = tag.get_text(separator=" ", strip=True)

                if SKIP_LINK_RE.search(abs_url):
                    continue
                if abs_url in seen_cands:
                    continue

                parsed   = urlparse(abs_url)
                same_dom = (
                    parsed.netloc == base_dom
                    or parsed.netloc.endswith("." + base_dom)
                    or base_dom.endswith("." + parsed.netloc)
                )
                is_ats       = bool(JOB_URL_RE.search(abs_url))
                career_link  = bool(CAREER_PAGE_RE.search(parsed.path))
                job_anchor   = bool(JOB_ANCHOR_RE.search(anchor_text))

                # For portal search results: only follow ATS links
                if stype == "search_results":
                    if not is_ats:
                        continue
                else:
                    # Accept link if it matches any strong signal:
                    #  • Known ATS URL pattern
                    #  • Same domain + career-page path
                    #  • Same domain + job-like anchor text
                    if not (is_ats or (same_dom and (career_link or job_anchor))):
                        continue

                # Quick seniority pre-screen (no network call)
                pre_text = anchor_text + " " + abs_url
                if _TOO_JUNIOR_RE.search(pre_text):
                    self.stats.skipped_junior += 1
                    continue
                if _TOO_SENIOR_RE.search(pre_text):
                    self.stats.skipped_senior += 1
                    continue

                seen_cands.add(abs_url)
                candidates.append(abs_url)

        # ── Phase C: Google fallback if no candidates on site ─────────────────
        if not candidates and stype != "search_results":
            parsed = urlparse(url)
            domain = parsed.netloc
            logger.info(f"[Scanner] {name}: no job links on site — trying Google fallback")
            google_urls = self._google_company_jobs(
                name, domain, settings.get("search_keywords", [])
            )
            candidates.extend(google_urls)

        self.stats.links_found += len(candidates)

        # ── Phase D: Delta filter — only process URLs new since last scan ──────
        known_urls    = self._get_known_urls(url)
        current_set   = set(candidates)
        is_first_scan = len(known_urls) == 0

        if is_first_scan:
            # First time seeing this source → evaluate up to MAX_JOBS_PER_SOURCE
            to_process = candidates[:MAX_JOBS_PER_SOURCE]
            logger.debug(f"[Delta] {name}: first scan — evaluating {len(to_process)} candidates")
        else:
            # Subsequent scan → only new URLs not in the snapshot
            new_urls   = [u for u in candidates if u not in known_urls]
            to_process = new_urls   # no cap: all new postings matter
            if new_urls:
                logger.info(
                    f"[Delta] {name}: {len(new_urls)} new URL(s) since last scan "
                    f"(+{len(new_urls)} of {len(candidates)} total)"
                )
            else:
                logger.debug(f"[Delta] {name}: no new job links since last scan — skipping evaluation")

        # ── Phase E: Pipeline evaluation ──────────────────────────────────────
        for job_url in to_process:
            if not self._running:
                break
            self._run_pipeline(job_url, settings)
            time.sleep(DELAY_BETWEEN_PAGES)

        jobs_delta = self.stats.jobs_saved - jobs_before

        # ── Update scan memory with the full snapshot of links seen this pass ─
        # Merges with old snapshot so URLs removed from the page don't
        # trigger false "new" signals if they reappear.
        self._update_source_memory(url, jobs_delta, job_url_snapshot=current_set)

        # ── Update CompanySource DB record (careers URL + scan stats) ─────────
        if self._app and stype == "career_page":
            try:
                with self._app.app_context():
                    from models.database import db, CompanySource
                    row = CompanySource.query.filter_by(source_url=url).first()
                    if row:
                        if careers_url != url:
                            row.careers_url = careers_url
                        row.last_scanned_at = datetime.utcnow()
                        row.scan_count = (row.scan_count or 0) + 1
                        db.session.commit()
            except Exception as _dbe:
                logger.debug(f"[Delta] DB update for {name}: {_dbe}")

        if jobs_delta:
            logger.info(f"[Scanner] ✦ {name}: {jobs_delta} new job(s) saved")

    # ── Full evaluation pipeline ──────────────────────────────────────────────

    def _run_pipeline(self, job_url: str, settings: dict):
        """
        Run a candidate URL through all filter layers.
        Gemini is NEVER called unless every pre-filter passes.
        """
        if not self._app:
            return

        with self._app.app_context():
            try:
                from models.database import db, ScanHistory, Application, JobPosting
                from services.dedup import is_duplicate as check_dup

                # ── Dedup: URL already processed? ─────────────────────────────
                if ScanHistory.query.filter_by(url=job_url).first():
                    self.stats.skipped_duplicate += 1
                    return

                city     = settings.get("location", "Mumbai").strip()
                keywords = settings.get("search_keywords", [])

                # ── Fetch page ─────────────────────────────────────────────────
                page_text, soup = _fetch_page(job_url)

                # ══ PRE-0/PRE-1 — Empty / thin JD ════════════════════════════
                if _pre_empty(page_text):
                    self.stats.skipped_empty += 1
                    self._record(db, job_url, "empty_jd")
                    logger.debug(f"[PRE] empty/thin JD: {job_url}")
                    return

                title = _extract_title(soup) if soup else ""

                # ══ L0 — Seniority (bi-directional) ══════════════════════════
                seniority_reason = _l0_seniority(title, page_text)
                if seniority_reason:
                    if "junior" in seniority_reason:
                        self.stats.skipped_junior += 1
                    else:
                        self.stats.skipped_senior += 1
                    self._record(db, job_url, seniority_reason.replace(" ", "_"))
                    logger.debug(f"[L0] {seniority_reason}: '{title[:60]}' {job_url}")
                    return

                # ══ L1a — PM keyword density ══════════════════════════════════
                if _l1a_pm_density(page_text):
                    self.stats.skipped_no_pm += 1
                    self._record(db, job_url, "no_pm_keywords")
                    logger.debug(f"[L1a] insufficient PM keywords: {job_url}")
                    return

                # ══ L1b — User keyword cross-check ════════════════════════════
                if _l1b_user_keywords(page_text, keywords):
                    self.stats.skipped_no_kw += 1
                    self._record(db, job_url, "no_user_keyword")
                    logger.debug(f"[L1b] none of user keywords found: {job_url}")
                    return

                # ══ L2 — Location hard gate ═══════════════════════════════════
                if _l2_location(page_text, city):
                    self.stats.skipped_no_loc += 1
                    self._record(db, job_url, "wrong_location")
                    logger.debug(f"[L2] city '{city}' not in JD: {job_url}")
                    return

                # ══ L3 — Role title function check ════════════════════════════
                if _l3_role_title(title):
                    self.stats.skipped_wrong_fn += 1
                    self._record(db, job_url, "non_pm_function")
                    logger.debug(f"[L3] non-PM role title '{title[:60]}'")
                    return

                # ══ L4 — Gemini AI evaluation ════════════════════════════════
                # Only reached if ALL pre-filters pass — no wasted API tokens.
                eval_text = page_text[:6000]
                from services.gemini_client import evaluate_job
                self.stats.jobs_sent_to_ai += 1
                result = evaluate_job(eval_text, job_metadata={"url": job_url})

                if not result.get("success"):
                    ft = result.get("filter_type", "ai_filter")
                    self.stats.jobs_ai_filtered += 1
                    self._record(db, job_url, ft or "ai_filter")
                    logger.debug(f"[L4] AI rejected ({ft}): {job_url}")
                    return

                data    = result["data"]
                grade   = data.get("grade", "F")
                role    = data.get("role", title or "Unknown")
                company = data.get("company", "Unknown")
                loc     = data.get("location", city)
                score   = (lambda v: float(v) if v is not None else None)(data.get("score"))
                rec     = data.get("recommendation", "")

                # ══ L5 — Grade gate ═══════════════════════════════════════════
                if grade not in ("A", "B"):
                    self.stats.jobs_ai_filtered += 1
                    self._record(db, job_url, f"grade_{grade}")
                    logger.debug(f"[L5] Grade {grade} — skip: {company} {role}")
                    return

                # ══ L6 — Post-AI location sanity ══════════════════════════════
                if city and loc and loc.lower() not in ("remote", "hybrid", "global", ""):
                    if (city.lower() not in loc.lower()
                            and loc.lower() not in city.lower()
                            and not _REMOTE_RE.search(loc)):
                        self._record(db, job_url, "ai_location_mismatch")
                        logger.debug(f"[L6] AI location '{loc}' ≠ '{city}'")
                        return

                logger.info(f"[Scanner] ✓ Grade {grade} | {company} | {role} | {loc}")

                # ── Dedup by content hash ──────────────────────────────────────
                from models.database import _normalize
                dedup = check_dup(job_url, company, role, loc)
                if dedup["is_duplicate"]:
                    self.stats.skipped_duplicate += 1
                    return

                # ── Persist ────────────────────────────────────────────────────
                posting = JobPosting(
                    job_hash=dedup["job_hash"],
                    company_normalized=_normalize(company),
                    role_normalized=_normalize(role),
                    location=loc,
                    first_seen_url=job_url,
                    first_seen_platform="auto_scan",
                    first_seen_date=datetime.utcnow(),
                    duplicate_urls="[]",
                    times_seen=1,
                )
                db.session.add(posting)
                db.session.flush()

                application = Application(
                    job_posting_id=posting.id,
                    company=company,
                    role=role,
                    location=loc,
                    url=job_url,
                    platform="auto_scan",
                    score=score,
                    grade=grade,
                    archetype=data.get("archetype"),
                    recommendation=rec,
                    visa_sponsorship=data.get("visa_sponsorship", "unclear"),
                    evaluation_json=json.dumps(data),
                    jd_text=eval_text,
                    status="Evaluated",
                )
                db.session.add(application)
                self._record(db, job_url, "new")
                db.session.commit()
                self.stats.jobs_saved += 1

                # ── Queue into agent ───────────────────────────────────────────
                try:
                    from services.agent import career_agent
                    career_agent.add_task(
                        app_id=application.id,
                        company=company, role=role,
                        url=job_url, jd_text=eval_text,
                    )
                    self.stats.jobs_queued += 1
                    logger.info(f"[Scanner] Queued for agent: {company} — {role}")
                except Exception as e:
                    logger.warning(f"[Scanner] Queue error: {e}")

            except Exception as exc:
                logger.warning(f"[Scanner] Pipeline error {job_url}: {exc}")
                self.stats.errors += 1
                try:
                    db.session.rollback()
                except Exception:
                    pass

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _record(self, db, url: str, result: str):
        """Save a ScanHistory row so the URL is never processed again."""
        try:
            from models.database import ScanHistory
            if not ScanHistory.query.filter_by(url=url).first():
                db.session.add(ScanHistory(
                    url=url, platform="auto_scan",
                    scanned_at=datetime.utcnow(), result=result,
                ))
                db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass


# ── Singleton ──────────────────────────────────────────────────────────────────
job_scanner = JobScanner()
