"""
job_scanner.py — Autonomous job discovery engine for Career-Ops.

Continuously crawls company career pages, filters for Performance
Marketing roles in the target city, evaluates against the full
filter stack (age, seniority, scam, legitimacy), saves matches,
and auto-queues Grade A/B jobs into the agent.
"""

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PROFILE_PATH         = os.path.join(os.path.dirname(__file__), "..", "data", "profile.json")
SOURCES_PATH         = os.path.join(os.path.dirname(__file__), "..", "data", "job_sources.json")
SETTINGS_PATH        = os.path.join(os.path.dirname(__file__), "..", "data", "scanner_settings.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT       = 15
DELAY_BETWEEN_SOURCES = 2.5
MAX_JOBS_PER_SOURCE   = 8
SCAN_INTERVAL_HOURS   = 3

# URL patterns that indicate individual job postings
JOB_LINK_RE = re.compile(
    r"(/job[s]?/|/position[s]?/|/opening[s]?/|/vacancies?/|/apply/|"
    r"/requisition|/role[s]?/|/opportunit|/career[s]?/[^/]+$|"
    r"lever\.co/[^/]+/|greenhouse\.io/|workday\.com/|"
    r"smartrecruiters\.com/|breezy\.hr/|ashbyhq\.com/|"
    r"jobs\.ashby\.com/|apply\.workable\.com/|"
    r"myworkdayjobs\.com/|taleo\.net/|icims\.com/)",
    re.IGNORECASE,
)

# Domains to skip as crawl sources (require login / block crawlers)
SKIP_SOURCE_DOMAINS = {
    "linkedin.com", "naukri.com", "indeed.com",
    "glassdoor.com", "monster.com", "shine.com",
    "twitter.com", "facebook.com", "instagram.com",
    "youtube.com",
}

# Links to never follow
SKIP_LINK_RE = re.compile(
    r"(linkedin\.com|twitter\.com|facebook\.com|instagram\.com|"
    r"youtube\.com|whatsapp|mailto:|tel:|javascript:|^#|"
    r"google\.com/maps|\.pdf$|\.zip$|\.png$|\.jpg$|/logout|/login|/signup)",
    re.IGNORECASE,
)

# Performance-marketing relevance keywords
# A discovered job page must contain at least one of these to proceed to Gemini
PM_KEYWORDS = [
    "performance marketing", "paid media", "paid search", "paid social",
    "google ads", "meta ads", "facebook ads", "programmatic",
    "sem", "ppc", "performance manager", "media manager",
    "digital marketing manager", "growth marketing", "demand generation",
    "biddable media", "performance lead", "media lead",
    "digital performance", "marketing manager",
]

PM_KEYWORD_RE = re.compile(
    "|".join(re.escape(k) for k in PM_KEYWORDS),
    re.IGNORECASE,
)

# Seniority keywords that disqualify a link before even fetching the page
SKIP_SENIORITY_RE = re.compile(
    r"\b(intern|internship|trainee|fresher|junior|jr\.?|"
    r"entry[- ]level|graduate|apprentice)\b",
    re.IGNORECASE,
)


@dataclass
class ScanStats:
    sources_checked: int = 0
    links_found: int = 0
    pm_relevant: int = 0
    jobs_evaluated: int = 0
    jobs_saved: int = 0
    jobs_queued: int = 0
    skipped_duplicate: int = 0
    skipped_filter: int = 0
    errors: int = 0
    current_source: str = ""
    status: str = "idle"
    last_run_start: Optional[str] = None
    last_run_end: Optional[str] = None


class JobScanner:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock   = threading.Lock()
        self._app    = None
        self.stats   = ScanStats()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, app):
        """Start continuous scanning (repeats every SCAN_INTERVAL_HOURS)."""
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
        """Trigger a single scan pass in a background thread."""
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
        return {
            "is_running": self._running,
            "status":     s.status,
            "current_source": s.current_source,
            "last_run_start": s.last_run_start,
            "last_run_end":   s.last_run_end,
            "stats": {
                "sources_checked": s.sources_checked,
                "links_found":     s.links_found,
                "pm_relevant":     s.pm_relevant,
                "jobs_evaluated":  s.jobs_evaluated,
                "jobs_saved":      s.jobs_saved,
                "jobs_queued":     s.jobs_queued,
                "skipped_duplicate": s.skipped_duplicate,
                "skipped_filter":    s.skipped_filter,
                "errors":            s.errors,
            },
        }

    # ── Settings helpers ──────────────────────────────────────────────────────

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

    # ── Internal loops ────────────────────────────────────────────────────────

    def _continuous_loop(self):
        while self._running:
            self._scan_pass()
            self.stats.status = "waiting"
            # Sleep in small increments so stop() is responsive
            sleep_total = SCAN_INTERVAL_HOURS * 3600
            slept = 0
            while slept < sleep_total and self._running:
                time.sleep(10)
                slept += 10
        self.stats.status = "idle"

    def _scan_pass(self):
        self.stats.last_run_start = datetime.now().isoformat()
        self.stats.status         = "scanning"
        settings = self.load_settings()
        location = settings.get("location", "")
        auto_start_agent = settings.get("auto_start_agent", True)

        sources = self._build_source_list(location)
        logger.info(f"[Scanner] Pass started — {len(sources)} sources, city: {location or 'any'}")

        for src in sources:
            if not self._running:
                break
            try:
                self._process_source(src, settings)
            except Exception as exc:
                logger.warning(f"[Scanner] Error on {src['name']}: {exc}")
                self.stats.errors += 1
            time.sleep(DELAY_BETWEEN_SOURCES)

        self.stats.last_run_end = datetime.now().isoformat()
        self.stats.current_source = ""

        # Auto-start the agent if we queued anything
        if auto_start_agent and self.stats.jobs_queued > 0 and self._app:
            try:
                from services.agent import career_agent
                if not career_agent.is_running:
                    career_agent.start(self._app)
                    logger.info("[Scanner] Auto-started agent.")
            except Exception as e:
                logger.warning(f"[Scanner] Could not auto-start agent: {e}")

        logger.info(
            f"[Scanner] Pass done — sources:{self.stats.sources_checked} "
            f"links:{self.stats.links_found} pm:{self.stats.pm_relevant} "
            f"saved:{self.stats.jobs_saved} queued:{self.stats.jobs_queued}"
        )

    # ── Source list building ──────────────────────────────────────────────────

    def _build_source_list(self, location: str) -> list:
        try:
            with open(SOURCES_PATH) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[Scanner] Cannot load sources: {e}")
            return []

        settings = self.load_settings()
        keywords = settings.get("search_keywords", ["Performance Marketing"])
        query    = keywords[0] if keywords else "Performance Marketing"

        sources = []
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

        # Company career pages (flat lists)
        flat_sector_keys = [
            "tech_platforms", "d2c_brands", "ecommerce", "quick_commerce",
            "fmcg_cpg", "consulting", "b2b_saas", "fintech", "uae_conglomerates",
            "edtech", "travel_hospitality",
        ]
        for key in flat_sector_keys:
            for item in data.get(key, []):
                if isinstance(item, dict):
                    careers = item.get("careers") or item.get("url", "")
                    _add(item.get("name", careers), careers, "career_page")

        # Nested regional: agency_networks, boutique_agencies
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

        return sources

    # ── Per-source processing ─────────────────────────────────────────────────

    def _process_source(self, source: dict, settings: dict):
        name = source["name"]
        url  = source["url"]
        self.stats.current_source = name
        self.stats.sources_checked += 1

        location = settings.get("location", "")
        logger.debug(f"[Scanner] Checking: {name}")

        job_links = self._find_job_links(url, source["type"])
        if not job_links:
            return

        self.stats.links_found += len(job_links)

        # Filter by location in URL/text if location is set
        if location:
            job_links = self._filter_by_location(job_links, location)

        for job_url in job_links[:MAX_JOBS_PER_SOURCE]:
            if not self._running:
                break
            self._evaluate_and_queue(job_url, settings)
            time.sleep(1.5)

    def _find_job_links(self, page_url: str, source_type: str) -> list:
        """Fetch a page and return job-posting links, pre-filtered for PM relevance."""
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code not in (200, 301, 302):
                return []
        except Exception as exc:
            logger.debug(f"[Scanner] Fetch failed {page_url}: {exc}")
            return []

        soup      = BeautifulSoup(resp.text, "html.parser")
        base_domain = urlparse(page_url).netloc
        links: set = set()

        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href:
                continue
            abs_url = urljoin(page_url, href)
            if SKIP_LINK_RE.search(abs_url):
                continue

            anchor_text = (tag.get_text(separator=" ") or "").strip()

            # Skip clearly junior/intern links
            if SKIP_SENIORITY_RE.search(anchor_text) or SKIP_SENIORITY_RE.search(abs_url):
                continue

            parsed = urlparse(abs_url)
            same_domain = (
                parsed.netloc == base_domain
                or parsed.netloc.endswith("." + base_domain)
                or base_domain.endswith("." + parsed.netloc)
            )
            is_known_ats = JOB_LINK_RE.search(abs_url)

            if source_type == "search_results":
                if is_known_ats:
                    links.add(abs_url)
            else:
                if (same_domain or is_known_ats) and JOB_LINK_RE.search(abs_url):
                    links.add(abs_url)

        return list(links)

    def _filter_by_location(self, links: list, location: str) -> list:
        """Keep links that mention the target city in their URL (best effort)."""
        city_slug = location.lower().replace(" ", "[-_]?")
        city_re   = re.compile(city_slug, re.IGNORECASE)
        matched   = [l for l in links if city_re.search(l)]
        # If none matched, return all (location appears in JD text, not URL)
        return matched if matched else links

    # ── Evaluation + auto-queue ───────────────────────────────────────────────

    def _evaluate_and_queue(self, job_url: str, settings: dict):
        if not self._app:
            return

        with self._app.app_context():
            try:
                from models.database import db, ScanHistory, Application, JobPosting
                from models.database import _normalize
                from services.job_parser import parse_job_input
                from services.gemini_client import evaluate_job
                from services.dedup import is_duplicate as check_dup

                # Layer 0: already scanned?
                if ScanHistory.query.filter_by(url=job_url).first():
                    self.stats.skipped_duplicate += 1
                    return

                # Fetch and extract the job text
                parsed = parse_job_input(job_url)
                if not parsed.get("success") or not parsed.get("text"):
                    self.stats.errors += 1
                    return

                text = parsed["text"]
                url  = parsed.get("url") or job_url

                # ── PM relevance pre-filter ────────────────────────────────
                # Only send to Gemini if the page text has PM keywords
                if not PM_KEYWORD_RE.search(text):
                    logger.debug(f"[Scanner] No PM keywords — skipped: {url}")
                    self.stats.skipped_filter += 1
                    # Still record the URL so we don't try it again
                    try:
                        db.session.add(ScanHistory(
                            url=url, platform="auto_scan",
                            scanned_at=datetime.utcnow(), result="not_pm",
                        ))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    return

                self.stats.pm_relevant += 1

                # ── Location pre-filter ────────────────────────────────────
                city = settings.get("location", "")
                if city:
                    city_re = re.compile(re.escape(city), re.IGNORECASE)
                    # Also check remote/hybrid
                    remote_re = re.compile(r"\b(remote|hybrid|work from home|wfh)\b", re.IGNORECASE)
                    if not city_re.search(text) and not remote_re.search(text):
                        logger.debug(f"[Scanner] City '{city}' not in JD — skipped: {url}")
                        self.stats.skipped_filter += 1
                        try:
                            db.session.add(ScanHistory(
                                url=url, platform="auto_scan",
                                scanned_at=datetime.utcnow(), result="wrong_location",
                            ))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                        return

                # ── Full AI evaluation (includes age + seniority + scam filters) ──
                result = evaluate_job(text)
                self.stats.jobs_evaluated += 1

                if not result.get("success"):
                    ft = result.get("filter_type", "unknown")
                    logger.debug(f"[Scanner] Filtered ({ft}): {url}")
                    self.stats.skipped_filter += 1
                    return

                data     = result["data"]
                grade    = data.get("grade", "F")
                company  = data.get("company", "Unknown")
                role     = data.get("role", "Unknown")
                location = data.get("location", "")
                score_raw = data.get("score")
                score    = float(score_raw) if score_raw is not None else None

                logger.info(f"[Scanner] {company} | {role} | Grade {grade}")

                # Dedup layers 2 & 3
                dedup = check_dup(url, company, role, location)
                if dedup["is_duplicate"]:
                    self.stats.skipped_duplicate += 1
                    return

                # Save JobPosting
                posting = JobPosting(
                    job_hash=dedup["job_hash"],
                    company_normalized=_normalize(company),
                    role_normalized=_normalize(role),
                    location=location,
                    first_seen_url=url,
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
                    location=location,
                    url=url,
                    platform="auto_scan",
                    score=score,
                    grade=grade,
                    archetype=data.get("archetype"),
                    recommendation=data.get("recommendation"),
                    visa_sponsorship=data.get("visa_sponsorship", "unclear"),
                    evaluation_json=json.dumps(data),
                    jd_text=text,
                    status="Evaluated",
                )
                db.session.add(application)
                db.session.add(ScanHistory(
                    url=url, platform="auto_scan",
                    scanned_at=datetime.utcnow(), result="new",
                ))
                db.session.commit()
                self.stats.jobs_saved += 1

                # Auto-queue if Grade A or B
                if grade in ("A", "B"):
                    try:
                        from services.agent import career_agent
                        task = career_agent.add_task(
                            app_id=application.id,
                            company=company,
                            role=role,
                            url=url or "",
                            jd_text=text,
                        )
                        if task is not None:
                            self.stats.jobs_queued += 1
                            logger.info(f"[Scanner] Queued for application: {company}")
                    except Exception as e:
                        logger.warning(f"[Scanner] Queue error: {e}")

            except Exception as exc:
                logger.warning(f"[Scanner] Eval error {job_url}: {exc}")
                self.stats.errors += 1
                try:
                    db.session.rollback()
                except Exception:
                    pass


# ── Singleton ─────────────────────────────────────────────────────────────────
job_scanner = JobScanner()
