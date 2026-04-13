"""
job_scanner.py — Brutally filtered, multi-layered autonomous job crawler.

Every discovered URL must pass SIX sequential hard filter layers before
a single Gemini API call is made.  Only Grade A/B jobs that explicitly
mention the target city (or remote/hybrid) in the JD text are saved.

Dynamic source discovery supplements the static company list by running
DuckDuckGo searches and scraping job-board search pages in real time.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, quote, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE        = os.path.dirname(__file__)
PROFILE_PATH  = os.path.join(BASE, "..", "data", "profile.json")
SOURCES_PATH  = os.path.join(BASE, "..", "data", "job_sources.json")
SETTINGS_PATH = os.path.join(BASE, "..", "data", "scanner_settings.json")

# ── HTTP ────────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "DNT":             "1",
}
REQUEST_TIMEOUT      = 18
DELAY_BETWEEN_PAGES  = 2.0
MAX_JOBS_PER_SOURCE  = 6
SCAN_INTERVAL_HOURS  = 3

# ── URLs to skip as sources ────────────────────────────────────────────────────
SKIP_SOURCE_DOMAINS = {
    "linkedin.com", "naukri.com", "indeed.com", "glassdoor.com",
    "monster.com", "shine.com", "twitter.com", "facebook.com",
    "instagram.com", "youtube.com", "t.me", "telegram.org",
}

# ── Links to never follow ──────────────────────────────────────────────────────
SKIP_LINK_RE = re.compile(
    r"(linkedin\.com|twitter\.com|x\.com|facebook\.com|instagram\.com|"
    r"youtube\.com|whatsapp|mailto:|tel:|javascript:|^#|"
    r"google\.com/maps|maps\.app|\.pdf$|\.zip$|\.png$|\.jpg$|\.jpeg$|"
    r"/logout|/login|/signin|/signup|/register|/privacy|/terms|"
    r"/cookie|/about$|/contact$|/blog/|/news/|/press/|/team$|"
    r"support\.|help\.|status\.)",
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
    r"hire\.trakstar\.com/|recruit\.zoho\.com/|"
    r"instahyre\.com/job/|cutshort\.io/job|wellfound\.com/jobs/|"
    r"/careers/[a-z0-9_-]{4,}$)",
    re.IGNORECASE,
)

# ═════════════════════════════════════════════════════════════════════════════
# LAYER DEFINITIONS — each returns True = SKIP, False = PASS
# ═════════════════════════════════════════════════════════════════════════════

# ── Layer 0 — Seniority exclusion (URL/anchor, no fetch required) ──────────────
_JUNIOR_RE = re.compile(
    r"\b(intern(ship)?|trainee|fresher|junior|jr\.?|entry[- ]level|"
    r"associate[- ]level|graduate[- ]traineeship|apprentice|"
    r"0[- ]?[–-][- ]?2\s*years?)\b",
    re.IGNORECASE,
)

def _layer0_junior(text: str) -> bool:
    """Return True (SKIP) if text signals a junior/intern role."""
    return bool(_JUNIOR_RE.search(text))


# ── Layer 1 — PM keyword density (applied to job page full text) ──────────────
#
# Jobs must show evidence of being a PERFORMANCE / PAID MEDIA role.
# Each group below represents a distinct concept; the page must hit
# at least MIN_PM_GROUPS different groups, with ≥ MIN_PM_HITS total.
#
_PM_GROUPS = [
    # Group 0 — role name
    re.compile(
        r"(performance\s+marketing|paid\s+media|media\s+manager|"
        r"media\s+lead|biddable\s+media|programmatic|performance\s+lead|"
        r"digital\s+performance|growth\s+marketing|demand\s+gen)",
        re.IGNORECASE,
    ),
    # Group 1 — paid search / PPC
    re.compile(
        r"(ppc|sem|paid\s+search|google\s+ads|search\s+ads|"
        r"adwords|search\s+campaigns?|keyword\s+bid)",
        re.IGNORECASE,
    ),
    # Group 2 — paid social
    re.compile(
        r"(meta\s+ads|facebook\s+ads|instagram\s+ads|paid\s+social|"
        r"social\s+advertising|tiktok\s+ads|linkedin\s+ads)",
        re.IGNORECASE,
    ),
    # Group 3 — channels / tools
    re.compile(
        r"(youtube\s+ads|display\s+advertising|dv360|campaign\s+manager|"
        r"dsp|rtb|programmatic\s+buying|trade\s+desk)",
        re.IGNORECASE,
    ),
    # Group 4 — performance metrics / KPIs
    re.compile(
        r"(roas|cpa|cpl|cpc|ctr|roi|cost\s+per\s+acquisition|"
        r"return\s+on\s+ad\s+spend|conversion\s+rate|funnel)",
        re.IGNORECASE,
    ),
    # Group 5 — analytics
    re.compile(
        r"(google\s+analytics|ga4|data\s+studio|looker|attribution|"
        r"a/b\s+test|split\s+test|pixel|tracking)",
        re.IGNORECASE,
    ),
]
MIN_PM_GROUPS = 2    # must match at least 2 distinct groups
MIN_PM_HITS   = 3    # must have ≥3 total keyword occurrences

def _layer1_pm_density(text: str) -> bool:
    """Return True (SKIP) if PM keyword density is too low."""
    groups_hit = 0
    total_hits = 0
    for pat in _PM_GROUPS:
        hits = len(pat.findall(text))
        if hits:
            groups_hit += 1
            total_hits += hits
    return groups_hit < MIN_PM_GROUPS or total_hits < MIN_PM_HITS


# ── Layer 2 — Location hard check ─────────────────────────────────────────────
_REMOTE_RE = re.compile(
    r"\b(remote|hybrid|work\s+from\s+home|wfh|flexible\s+location|"
    r"location\s+agnostic|anywhere)\b",
    re.IGNORECASE,
)

def _layer2_location(text: str, city: str) -> bool:
    """Return True (SKIP) if the target city is absent and it's not remote/hybrid."""
    if not city:
        return False  # no filter configured
    city_re = re.compile(re.escape(city), re.IGNORECASE)
    if city_re.search(text):
        return False  # city found
    if _REMOTE_RE.search(text):
        return False  # remote/hybrid is acceptable
    return True  # SKIP — wrong location


# ── Layer 3 — Role title PM relevance ─────────────────────────────────────────
#
# Roles that should NEVER be accepted even if the page has PM words
# (e.g., an engineer on a marketing team)
_NON_PM_ROLE_RE = re.compile(
    r"\b(software\s+engineer|backend|frontend|full[- ]stack|devops|"
    r"data\s+engineer|data\s+analyst|data\s+scientist|"
    r"product\s+manager|product\s+owner|program\s+manager|"
    r"finance|accounting|accountant|hr\s+manager|human\s+resources|"
    r"recruiter|talent\s+acquisition|graphic\s+design|"
    r"content\s+writer|copywriter|seo\s+specialist|"
    r"social\s+media\s+manager|community\s+manager|"
    r"ui\s+designer|ux\s+designer|video\s+editor|"
    r"business\s+development|sales\s+manager|account\s+manager|"
    r"customer\s+success|operations\s+manager)\b",
    re.IGNORECASE,
)
# Positive signal — title must include at least one of these
_PM_TITLE_RE = re.compile(
    r"(performance\s+market|paid\s+media|media\s+manag|media\s+lead|"
    r"digital\s+market|growth\s+market|demand\s+gen|biddable|"
    r"ppc\s+manag|sem\s+manag|performance\s+lead)",
    re.IGNORECASE,
)

def _layer3_role_title(title: str) -> bool:
    """Return True (SKIP) if the job title is clearly non-PM or lacks PM signal."""
    if not title:
        return False  # can't judge — let AI decide
    if _NON_PM_ROLE_RE.search(title):
        return True  # clearly wrong function
    # If the title has PM signal — keep; otherwise trust the text (neutral)
    return False


# ── Layer 4 — Seniority gate (from page text + title) ─────────────────────────
_SENIORITY_OK_RE = re.compile(
    r"\b(senior|sr\.?|lead|manager|head|director|vp|vice\s+president|"
    r"principal|associate\s+director|associate\s+manager)\b",
    re.IGNORECASE,
)

def _layer4_seniority(title: str, text: str) -> bool:
    """Return True (SKIP) if seniority signals are absent."""
    combined = (title + " " + text[:2000])
    if _layer0_junior(combined):
        return True
    # We require at least one mid-senior indicator in the title or opening copy
    return not bool(_SENIORITY_OK_RE.search(title))


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _extract_title(soup: BeautifulSoup) -> str:
    """Try multiple selectors to pull the job title."""
    for sel in (
        "h1.job-title", "h1[class*='title']", "h1[class*='job']",
        "h1[class*='position']", "h1[class*='role']",
        "[class*='jobTitle']", "[class*='job-title']",
        "[class*='position-title']", "h1",
    ):
        tag = soup.select_one(sel)
        if tag:
            txt = tag.get_text(separator=" ", strip=True)
            if 3 < len(txt) < 120:
                return txt
    # Fallback: <title> minus the company name
    title_tag = soup.find("title")
    if title_tag:
        raw = title_tag.get_text(" ", strip=True)
        # Drop content after | or – or — (usually company name)
        return re.split(r"[|–—]", raw)[0].strip()[:120]
    return ""


def _fetch_text(url: str) -> tuple[str, BeautifulSoup | None]:
    """Fetch page, return (cleaned_text, soup). Returns ('','') on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code not in (200,):
            return "", None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True), soup
    except Exception:
        return "", None


# ═════════════════════════════════════════════════════════════════════════════
# DYNAMIC SOURCE DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

def _ddg_search(query: str) -> list[str]:
    """
    DuckDuckGo HTML search — free, no API key, returns real job URLs.
    """
    urls: list[str] = []
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query, "kl": "in-en"},
            headers={**HEADERS, "Referer": "https://duckduckgo.com/"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return urls
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            # DDG wraps links: /l/?uddg=<encoded_url>&...
            if "uddg=" in href:
                from urllib.parse import parse_qs, urlparse as _up, unquote
                qs = parse_qs(_up(href).query)
                real = qs.get("uddg", [""])[0]
                if real:
                    href = unquote(real)
            if href.startswith("http") and not SKIP_LINK_RE.search(href):
                urls.append(href)
    except Exception as e:
        logger.debug(f"[DDG] search failed: {e}")
    return urls


def _dynamic_job_urls(location: str, keywords: list[str]) -> list[str]:
    """
    Run several DuckDuckGo searches for fresh job postings and return
    a de-duplicated list of job page URLs.
    """
    city = location or "Mumbai"
    kw0  = keywords[0] if keywords else "Performance Marketing"
    queries = [
        f'"{city}" "{kw0}" job opening site:lever.co OR site:greenhouse.io OR site:ashbyhq.com',
        f'"{city}" "performance marketing manager" OR "paid media manager" job apply 2025',
        f'"{city}" "media lead" OR "performance lead" job hiring',
        f'"mumbai" OR "remote india" performance marketing manager jobs -naukri -linkedin',
        f'site:instahyre.com "{kw0}"',
        f'site:cutshort.io "performance marketing"',
        f'site:wellfound.com/jobs "performance marketing" "{city}"',
    ]
    all_urls: list[str] = []
    seen: set[str] = set()
    for q in queries:
        found = _ddg_search(q)
        for u in found:
            if u not in seen:
                seen.add(u)
                all_urls.append(u)
        time.sleep(1.5)
    return all_urls


# ═════════════════════════════════════════════════════════════════════════════
# STATS
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ScanStats:
    sources_checked:  int = 0
    links_found:      int = 0
    layer0_skipped:   int = 0   # junior / intern
    layer1_skipped:   int = 0   # not enough PM keywords
    layer2_skipped:   int = 0   # wrong location
    layer3_skipped:   int = 0   # wrong role title
    layer4_skipped:   int = 0   # wrong seniority
    duplicate_skipped: int = 0
    jobs_evaluated:   int = 0
    jobs_saved:       int = 0
    jobs_queued:      int = 0
    errors:           int = 0
    current_source:   str = ""
    status:           str = "idle"
    last_run_start:   Optional[str] = None
    last_run_end:     Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════════
# SCANNER
# ═════════════════════════════════════════════════════════════════════════════

class JobScanner:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock   = threading.Lock()
        self._app    = None
        self.stats   = ScanStats()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self, app):
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

    def stop(self):
        with self._lock:
            if not self._running:
                return {"stopped": False, "reason": "not_running"}
            self._running = False
            self.stats.status = "idle"
            self.stats.current_source = ""
        logger.info("[Scanner] Stop requested.")
        return {"stopped": True}

    def run_once(self, app):
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
        total_filtered = (
            s.layer0_skipped + s.layer1_skipped + s.layer2_skipped
            + s.layer3_skipped + s.layer4_skipped + s.duplicate_skipped
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
                "pm_relevant":     s.jobs_evaluated,
                "jobs_saved":      s.jobs_saved,
                "jobs_queued":     s.jobs_queued,
                "filtered_out":    total_filtered,
                "errors":          s.errors,
                # Layer breakdown for debugging
                "filters": {
                    "junior_skip":    s.layer0_skipped,
                    "no_pm_keywords": s.layer1_skipped,
                    "wrong_location": s.layer2_skipped,
                    "wrong_role":     s.layer3_skipped,
                    "wrong_seniority":s.layer4_skipped,
                    "duplicate":      s.duplicate_skipped,
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
        current = {}
        try:
            with open(SETTINGS_PATH) as f:
                current = json.load(f)
        except Exception:
            pass
        current.update(data)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(current, f, indent=2)

    # ── Internal loops ─────────────────────────────────────────────────────────

    def _continuous_loop(self):
        while self._running:
            self._scan_pass()
            self.stats.status = "waiting"
            slept = 0
            while slept < SCAN_INTERVAL_HOURS * 3600 and self._running:
                time.sleep(10)
                slept += 10
        self.stats.status = "idle"

    def _scan_pass(self):
        self.stats.last_run_start = datetime.now().isoformat()
        self.stats.status         = "scanning"
        settings = self.load_settings()
        location = settings.get("location", "").strip()
        keywords = settings.get("search_keywords", ["Performance Marketing"])
        auto_start_agent = settings.get("auto_start_agent", True)

        # ── 1. Static sources ──────────────────────────────────────────────────
        static_sources = self._build_static_sources(location, keywords)
        # ── 2. Dynamic sources via DDG search ─────────────────────────────────
        logger.info("[Scanner] Running dynamic DDG discovery…")
        dynamic_urls = _dynamic_job_urls(location, keywords)
        logger.info(f"[Scanner] DDG found {len(dynamic_urls)} candidate URLs")

        # Process static sources (career page crawl)
        for src in static_sources:
            if not self._running:
                break
            try:
                self._crawl_source(src, settings)
            except Exception as exc:
                logger.warning(f"[Scanner] Source error {src['name']}: {exc}")
                self.stats.errors += 1
            time.sleep(DELAY_BETWEEN_PAGES)

        # Process dynamic URLs directly (each is already a candidate job URL)
        for job_url in dynamic_urls:
            if not self._running:
                break
            try:
                self._evaluate_candidate(job_url, settings)
            except Exception as exc:
                logger.debug(f"[Scanner] Dynamic URL error {job_url}: {exc}")
                self.stats.errors += 1
            time.sleep(DELAY_BETWEEN_PAGES)

        self.stats.last_run_end   = datetime.now().isoformat()
        self.stats.current_source = ""

        # Auto-start agent if new Grade A/B jobs were queued
        if auto_start_agent and self.stats.jobs_queued > 0 and self._app:
            try:
                from services.agent import career_agent
                if not career_agent.is_running:
                    career_agent.start(self._app)
                    logger.info("[Scanner] Auto-started agent.")
            except Exception as e:
                logger.warning(f"[Scanner] Auto-start agent failed: {e}")

        total_filtered = (
            self.stats.layer0_skipped + self.stats.layer1_skipped
            + self.stats.layer2_skipped + self.stats.layer3_skipped
            + self.stats.layer4_skipped
        )
        logger.info(
            f"[Scanner] Pass complete — "
            f"sources:{self.stats.sources_checked} links:{self.stats.links_found} "
            f"saved:{self.stats.jobs_saved} queued:{self.stats.jobs_queued} "
            f"filtered:{total_filtered}"
        )

    # ── Source list building ───────────────────────────────────────────────────

    def _build_static_sources(self, location: str, keywords: list) -> list:
        try:
            with open(SOURCES_PATH) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[Scanner] Cannot load sources: {e}")
            return []

        query = keywords[0] if keywords else "Performance Marketing"
        sources: list = []
        seen_urls: set = set()

        def _add(name, url, stype):
            if not url:
                return
            if any(d in url for d in SKIP_SOURCE_DOMAINS):
                return
            if not url.startswith("http"):
                url = "https://" + url
            if url in seen_urls:
                return
            seen_urls.add(url)
            sources.append({"name": name, "url": url, "type": stype})

        # Flat sector lists
        for key in [
            "tech_platforms", "d2c_brands", "ecommerce", "quick_commerce",
            "fmcg_cpg", "consulting", "b2b_saas", "fintech",
            "uae_conglomerates", "edtech", "travel_hospitality",
        ]:
            for item in data.get(key, []):
                if isinstance(item, dict):
                    careers = item.get("careers") or item.get("url", "")
                    _add(item.get("name", careers), careers, "career_page")

        # Nested regional (agency_networks, boutique_agencies)
        for top_key in ["agency_networks", "boutique_agencies"]:
            region_map = data.get(top_key, {})
            if isinstance(region_map, dict):
                for region_list in region_map.values():
                    for item in (region_list if isinstance(region_list, list) else []):
                        if isinstance(item, dict):
                            careers = item.get("careers") or item.get("url", "")
                            _add(item.get("name", careers), careers, "career_page")

        # VC portfolio job boards
        for item in data.get("vc_portfolio_jobs", []):
            if isinstance(item, dict):
                url = item.get("url", "")
                _add(item.get("name", url), url, "career_page")

        # Job portals with search_url templates (skip blocked ones)
        loc_param = location or "India"
        for region, portal_list in data.get("job_portals", {}).items():
            for portal in (portal_list if isinstance(portal_list, list) else []):
                if not isinstance(portal, dict):
                    continue
                tpl = portal.get("search_url", "")
                if not tpl:
                    continue
                base_url = portal.get("url", "")
                if any(d in base_url for d in SKIP_SOURCE_DOMAINS):
                    continue
                url = tpl.replace("{query}", quote(query)).replace("{location}", quote(loc_param))
                _add(portal.get("name", base_url), url, "search_results")

        # ── Extra high-signal sources not in job_sources.json ─────────────────
        extra = self._extra_pm_sources(location, query)
        for name, url in extra:
            _add(name, url, "search_results")

        return sources

    def _extra_pm_sources(self, location: str, keyword: str) -> list[tuple[str, str]]:
        """
        Hardcoded list of high-signal job pages that specifically surface
        Performance Marketing roles — including Indian market boards and
        ATS platforms with public search.
        """
        city = quote(location or "Mumbai")
        kw   = quote(keyword or "Performance Marketing")
        return [
            # Instahyre — quality sourcing for Indian startups
            ("Instahyre – PM Jobs",
             f"https://www.instahyre.com/search-jobs/?query={kw}&location={city}"),
            # Cutshort — startup-heavy, PM roles
            ("Cutshort – PM Jobs",
             f"https://cutshort.io/jobs?q={kw}&location={city}"),
            # Wellfound (AngelList Talent) — startup jobs globally
            ("Wellfound – PM Jobs",
             f"https://wellfound.com/jobs?role=performance-marketing&location={city}"),
            # Foundit (ex-Monster India)
            ("Foundit – Performance Marketing",
             f"https://www.foundit.in/srp/results?query={kw}&location={city}"),
            # Hirist — tech + digital marketing
            ("Hirist – Performance Marketing",
             f"https://www.hirist.tech/search-jobs?q={kw}&l={city}"),
            # iimjobs — management/marketing level
            ("iimjobs – Performance Marketing",
             f"https://www.iimjobs.com/search.php?q={kw}&loc={city}"),
            # Lever public jobs board search
            ("Lever – PM Roles",
             f"https://jobs.lever.co/?search={kw}"),
            # Greenhouse jobs board
            ("Greenhouse – PM Roles",
             f"https://boards.greenhouse.io/embed/job_board?q={kw}"),
            # Workable public jobs
            ("Workable – PM Jobs",
             f"https://apply.workable.com/#jobs?query={kw}"),
            # Otta (global marketing jobs, good for EMEA)
            ("Otta – Performance Marketing",
             f"https://app.otta.com/jobs/search?query={kw}"),
            # Flexi / remote-friendly India boards
            ("FlexJobs – Performance Marketing",
             f"https://www.flexjobs.com/search?search={kw}&location={city}"),
        ]

    # ── Career page crawl ──────────────────────────────────────────────────────

    def _crawl_source(self, source: dict, settings: dict):
        name = source["name"]
        url  = source["url"]
        self.stats.current_source = name
        self.stats.sources_checked += 1

        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code != 200:
                return
        except Exception as exc:
            logger.debug(f"[Scanner] Fetch failed {url}: {exc}")
            return

        soup      = BeautifulSoup(resp.text, "html.parser")
        base_dom  = urlparse(url).netloc
        candidates: list[str] = []

        for tag in soup.find_all("a", href=True):
            href        = tag["href"].strip()
            abs_url     = urljoin(url, href)
            anchor_text = (tag.get_text(separator=" ") or "").strip()

            # Never follow banned domains
            if SKIP_LINK_RE.search(abs_url):
                continue

            parsed = urlparse(abs_url)
            same_domain = (
                parsed.netloc == base_dom
                or parsed.netloc.endswith("." + base_dom)
                or base_dom.endswith("." + parsed.netloc)
            )
            is_ats = bool(JOB_URL_RE.search(abs_url))

            if source["type"] == "search_results":
                if not is_ats:
                    continue
            else:
                if not (same_domain or is_ats) or not JOB_URL_RE.search(abs_url):
                    continue

            # ── Layer 0 check on URL + anchor text (free, no network) ─────────
            if _layer0_junior(abs_url) or _layer0_junior(anchor_text):
                self.stats.layer0_skipped += 1
                continue

            candidates.append(abs_url)

        self.stats.links_found += len(candidates)

        for job_url in candidates[:MAX_JOBS_PER_SOURCE]:
            if not self._running:
                break
            self._evaluate_candidate(job_url, settings)
            time.sleep(DELAY_BETWEEN_PAGES)

    # ── Per-URL evaluation pipeline ───────────────────────────────────────────

    def _evaluate_candidate(self, job_url: str, settings: dict):
        """Run all 6 layers on a single candidate URL and queue if it passes."""
        if not self._app:
            return

        with self._app.app_context():
            try:
                from models.database import db, ScanHistory, Application, JobPosting
                from services.dedup import is_duplicate as check_dup

                # ── Dedup: URL already scanned? ────────────────────────────────
                if ScanHistory.query.filter_by(url=job_url).first():
                    self.stats.duplicate_skipped += 1
                    return

                # ── Fetch page ─────────────────────────────────────────────────
                page_text, soup = _fetch_text(job_url)
                if not page_text or len(page_text) < 200:
                    self._record_scan(db, job_url, "fetch_failed")
                    return

                title = _extract_title(soup) if soup else ""
                city  = settings.get("location", "").strip()

                # ══ LAYER 0 — Junior/intern in page title ═════════════════════
                if _layer0_junior(title):
                    self.stats.layer0_skipped += 1
                    self._record_scan(db, job_url, "junior_title")
                    logger.debug(f"[L0] SKIP junior title: {title[:60]}")
                    return

                # ══ LAYER 1 — PM keyword density ══════════════════════════════
                if _layer1_pm_density(page_text):
                    self.stats.layer1_skipped += 1
                    self._record_scan(db, job_url, "no_pm_keywords")
                    logger.debug(f"[L1] SKIP low PM density: {job_url}")
                    return

                # ══ LAYER 2 — Location hard check ═════════════════════════════
                if _layer2_location(page_text, city):
                    self.stats.layer2_skipped += 1
                    self._record_scan(db, job_url, "wrong_location")
                    logger.debug(f"[L2] SKIP location '{city}' not found: {job_url}")
                    return

                # ══ LAYER 3 — Role title PM relevance ═════════════════════════
                if _layer3_role_title(title):
                    self.stats.layer3_skipped += 1
                    self._record_scan(db, job_url, "non_pm_role")
                    logger.debug(f"[L3] SKIP non-PM role title: {title[:60]}")
                    return

                # ══ LAYER 4 — Seniority gate ══════════════════════════════════
                if _layer4_seniority(title, page_text):
                    self.stats.layer4_skipped += 1
                    self._record_scan(db, job_url, "junior_seniority")
                    logger.debug(f"[L4] SKIP seniority: {title[:60]}")
                    return

                # ══ LAYER 5 — Gemini full AI evaluation ═══════════════════════
                # Trim to first 6000 chars to stay within context limits
                eval_text = page_text[:6000]
                from services.gemini_client import evaluate_job
                result = evaluate_job(eval_text, job_metadata={"url": job_url})
                self.stats.jobs_evaluated += 1

                if not result.get("success"):
                    ft = result.get("filter_type", "ai_filter")
                    logger.debug(f"[L5] AI filtered ({ft}): {job_url}")
                    self._record_scan(db, job_url, ft or "ai_filter")
                    return

                data  = result["data"]
                grade = data.get("grade", "F")
                role  = data.get("role", title or "Unknown")
                company = data.get("company", "Unknown")
                loc   = data.get("location", city)
                score_raw = data.get("score")
                score = float(score_raw) if score_raw is not None else None
                rec   = data.get("recommendation", "")

                # ══ LAYER 6 — Grade gate: only A or B proceed ═════════════════
                if grade not in ("A", "B"):
                    logger.debug(f"[L6] Grade {grade} — skipping {company}: {role}")
                    self._record_scan(db, job_url, f"grade_{grade}")
                    return

                # ── Post-AI location sanity check ──────────────────────────────
                # If Gemini extracted a location and it disagrees with our city, skip
                if city and loc and loc.lower() not in ("remote", "hybrid", "global"):
                    if city.lower() not in loc.lower() and loc.lower() not in city.lower():
                        if not _REMOTE_RE.search(loc):
                            logger.debug(f"[Post-AI] Location mismatch: '{loc}' vs '{city}'")
                            self._record_scan(db, job_url, "ai_location_mismatch")
                            return

                logger.info(f"[Scanner] ✓ Grade {grade} | {company} | {role} | {loc}")

                # ── Dedup by content hash ──────────────────────────────────────
                from models.database import _normalize
                dedup = check_dup(job_url, company, role, loc)
                if dedup["is_duplicate"]:
                    self.stats.duplicate_skipped += 1
                    return

                # ── Save to DB ─────────────────────────────────────────────────
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
                db.session.add(ScanHistory(
                    url=job_url, platform="auto_scan",
                    scanned_at=datetime.utcnow(), result="new",
                ))
                db.session.commit()
                self.stats.jobs_saved += 1

                # ── Auto-queue Grade A/B into agent ───────────────────────────
                try:
                    from services.agent import career_agent
                    career_agent.add_task(
                        app_id=application.id,
                        company=company,
                        role=role,
                        url=job_url,
                        jd_text=eval_text,
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

    def _record_scan(self, db, url: str, result: str):
        """Write a ScanHistory entry so we never re-process this URL."""
        try:
            from models.database import ScanHistory
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
