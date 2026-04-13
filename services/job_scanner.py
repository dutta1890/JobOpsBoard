"""
job_scanner.py — Autonomous job discovery engine for Career-Ops.

Crawls company career pages and job sources, finds individual job
postings, evaluates them against the user's profile, and auto-queues
grade A/B results into the agent for application.
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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "profile.json")
SOURCES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "job_sources.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_TIMEOUT = 12
DELAY_BETWEEN_SOURCES = 2.0
MAX_JOBS_PER_SOURCE = 8
SCAN_INTERVAL_HOURS = 3

JOB_LINK_PATTERNS = re.compile(
    r"(/job[s]?/|/position[s]?/|/opening[s]?/|/career[s]?/|"
    r"/vacancy|/vacancies/|/apply/|/requisition|/role[s]?/|"
    r"/opportunity|/opportunities/|lever\.co/|greenhouse\.io/|"
    r"workday\.com/|smartrecruiters\.com/|breezy\.hr/|"
    r"ashbyhq\.com/|jobs\.ashby\.com/|apply\.workable\.com/)",
    re.IGNORECASE,
)

SKIP_PATTERNS = re.compile(
    r"(linkedin\.com|twitter\.com|facebook\.com|instagram\.com|"
    r"youtube\.com|whatsapp|mailto:|tel:|javascript:|#|"
    r"google\.com/maps|apple\.com|android\.com|\.pdf$|\.zip$)",
    re.IGNORECASE,
)

SKIP_SOURCE_DOMAINS = {
    "linkedin.com", "naukri.com", "indeed.com",
    "glassdoor.com", "monster.com", "shine.com",
}


@dataclass
class ScanStats:
    sources_checked: int = 0
    jobs_discovered: int = 0
    jobs_evaluated: int = 0
    jobs_saved: int = 0
    jobs_queued: int = 0
    jobs_skipped_duplicate: int = 0
    jobs_skipped_filter: int = 0
    errors: int = 0
    current_source: str = ""
    status: str = "idle"
    last_run_start: Optional[str] = None
    last_run_end: Optional[str] = None


class JobScanner:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._app = None
        self.stats = ScanStats()

    # ── Public control API ────────────────────────────────────────────────────

    def start(self, app):
        with self._lock:
            if self._running:
                return {"started": False, "reason": "already_running"}
            self._running = True
            self._app = app
            self.stats = ScanStats(status="running")
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="job_scanner"
            )
            self._thread.start()
        logger.info("[JobScanner] Started.")
        return {"started": True}

    def stop(self):
        with self._lock:
            if not self._running:
                return {"stopped": False, "reason": "not_running"}
            self._running = False
            self.stats.status = "idle"
            self.stats.current_source = ""
        logger.info("[JobScanner] Stop requested.")
        return {"stopped": True}

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        s = self.stats
        return {
            "is_running": self._running,
            "status": s.status,
            "current_source": s.current_source,
            "last_run_start": s.last_run_start,
            "last_run_end": s.last_run_end,
            "stats": {
                "sources_checked": s.sources_checked,
                "jobs_discovered": s.jobs_discovered,
                "jobs_evaluated": s.jobs_evaluated,
                "jobs_saved": s.jobs_saved,
                "jobs_queued": s.jobs_queued,
                "jobs_skipped_duplicate": s.jobs_skipped_duplicate,
                "jobs_skipped_filter": s.jobs_skipped_filter,
                "errors": s.errors,
            },
        }

    def run_once(self, app):
        """Trigger a single scan pass (non-blocking, spawns a thread)."""
        with self._lock:
            if self._running:
                return {"started": False, "reason": "scan_already_running"}
            self._running = True
            self._app = app
            self.stats = ScanStats(status="running")

        def _once():
            try:
                self._scan_all()
            finally:
                with self._lock:
                    self._running = False
                    self.stats.status = "idle"
                    self.stats.current_source = ""

        t = threading.Thread(target=_once, daemon=True, name="job_scanner_once")
        t.start()
        return {"started": True, "mode": "single_pass"}

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _run_loop(self):
        while self._running:
            self._scan_all()
            # Sleep between full passes, checking for stop signal
            sleep_seconds = SCAN_INTERVAL_HOURS * 3600
            slept = 0
            while slept < sleep_seconds and self._running:
                time.sleep(5)
                slept += 5
        self.stats.status = "idle"

    def _scan_all(self):
        self.stats.last_run_start = datetime.now().isoformat()
        self.stats.status = "scanning"
        logger.info("[JobScanner] Scan pass started.")

        sources = self._load_sources()
        logger.info(f"[JobScanner] {len(sources)} sources to check.")

        for source in sources:
            if not self._running:
                break
            try:
                self._process_source(source)
            except Exception as exc:
                logger.warning(f"[JobScanner] Error on source {source.get('name')}: {exc}")
                self.stats.errors += 1
            time.sleep(DELAY_BETWEEN_SOURCES)

        self.stats.last_run_end = datetime.now().isoformat()
        self.stats.current_source = ""
        self.stats.status = "idle" if not self._running else "waiting"
        logger.info(
            f"[JobScanner] Pass complete. "
            f"Checked: {self.stats.sources_checked}, "
            f"Found: {self.stats.jobs_discovered}, "
            f"Saved: {self.stats.jobs_saved}, "
            f"Queued: {self.stats.jobs_queued}"
        )

    # ── Source loading ────────────────────────────────────────────────────────

    def _load_sources(self) -> list:
        """Return a flat list of crawlable sources with their URLs."""
        try:
            with open(SOURCES_PATH) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"[JobScanner] Could not load sources: {e}")
            return []

        profile = self._load_profile()
        query = self._build_search_query(profile)
        locations = self._build_location_list(profile)

        sources = []

        # ── Company career pages ───────────────────────────────────────────────
        for sector_key, items in data.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                careers_url = item.get("careers") or item.get("url")
                if not careers_url:
                    continue
                if any(skip in careers_url for skip in SKIP_SOURCE_DOMAINS):
                    continue
                if not careers_url.startswith("http"):
                    careers_url = "https://" + careers_url
                sources.append({
                    "name": item.get("name", careers_url),
                    "url": careers_url,
                    "type": "career_page",
                })

        # ── Regional portals with search_url templates ──────────────────────
        portals = data.get("job_portals", {})
        for region, portal_list in portals.items():
            if not isinstance(portal_list, list):
                continue
            for portal in portal_list:
                if not isinstance(portal, dict):
                    continue
                search_url_tpl = portal.get("search_url", "")
                if not search_url_tpl:
                    continue
                domain = portal.get("url", "")
                if any(skip in domain for skip in SKIP_SOURCE_DOMAINS):
                    continue
                # Build one search URL per location
                loc = locations[0] if locations else "India"
                url = (
                    search_url_tpl
                    .replace("{query}", requests.utils.quote(query))
                    .replace("{location}", requests.utils.quote(loc))
                )
                sources.append({
                    "name": portal.get("name", domain),
                    "url": url,
                    "type": "search_results",
                })

        # ── Nested regional categories ────────────────────────────────────────
        for top_key in ["boutique_agencies", "agency_networks"]:
            region_map = data.get(top_key, {})
            if not isinstance(region_map, dict):
                continue
            for region, items in region_map.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    careers_url = item.get("careers") or item.get("url")
                    if not careers_url:
                        continue
                    if any(skip in careers_url for skip in SKIP_SOURCE_DOMAINS):
                        continue
                    if not careers_url.startswith("http"):
                        careers_url = "https://" + careers_url
                    sources.append({
                        "name": item.get("name", careers_url),
                        "url": careers_url,
                        "type": "career_page",
                    })

        # Deduplicate by URL
        seen = set()
        unique = []
        for s in sources:
            if s["url"] not in seen:
                seen.add(s["url"])
                unique.append(s)

        return unique

    def _load_profile(self) -> dict:
        try:
            with open(PROFILE_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    def _build_search_query(self, profile: dict) -> str:
        roles = profile.get("target_roles", [])
        if roles:
            return roles[0]
        return profile.get("title", "Performance Marketing Manager")

    def _build_location_list(self, profile: dict) -> list:
        locs = profile.get("target_locations", {})
        result = []
        for region, info in locs.items():
            cities = info.get("cities", []) if isinstance(info, dict) else []
            result.extend(cities)
        if not result:
            result.append(profile.get("location", "India"))
        return result

    # ── Per-source processing ─────────────────────────────────────────────────

    def _process_source(self, source: dict):
        name = source["name"]
        url = source["url"]
        self.stats.current_source = name
        self.stats.sources_checked += 1

        logger.info(f"[JobScanner] Checking: {name}")

        job_links = self._find_job_links(url, source["type"])
        if not job_links:
            return

        self.stats.jobs_discovered += len(job_links)
        logger.info(f"[JobScanner] Found {len(job_links)} links on {name}")

        for job_url in job_links[:MAX_JOBS_PER_SOURCE]:
            if not self._running:
                break
            self._evaluate_and_queue(job_url)
            time.sleep(1.5)

    def _find_job_links(self, page_url: str, source_type: str) -> list:
        """Fetch a page and extract links that look like individual job postings."""
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                return []
        except Exception as exc:
            logger.debug(f"[JobScanner] Fetch failed for {page_url}: {exc}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        base_domain = urlparse(page_url).netloc

        links = set()
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href:
                continue

            abs_url = urljoin(page_url, href)

            if SKIP_PATTERNS.search(abs_url):
                continue

            # For search result pages: accept any external job board link
            if source_type == "search_results":
                if JOB_LINK_PATTERNS.search(abs_url):
                    links.add(abs_url)
                continue

            # For career pages: look for job links on same domain OR known ATS platforms
            parsed = urlparse(abs_url)
            is_same_domain = (
                parsed.netloc == base_domain
                or parsed.netloc.endswith("." + base_domain)
                or base_domain.endswith("." + parsed.netloc)
            )
            is_known_ats = JOB_LINK_PATTERNS.search(abs_url)

            if is_same_domain or is_known_ats:
                if JOB_LINK_PATTERNS.search(abs_url) or self._looks_like_job_link(tag):
                    links.add(abs_url)

        return list(links)

    def _looks_like_job_link(self, tag) -> bool:
        """Check anchor text for job-related keywords."""
        text = (tag.get_text() or "").strip().lower()
        keywords = [
            "apply", "view job", "see job", "job detail", "open position",
            "apply now", "view role", "see opening", "learn more",
        ]
        return any(kw in text for kw in keywords)

    # ── Evaluation + auto-queue ───────────────────────────────────────────────

    def _evaluate_and_queue(self, job_url: str):
        """Run a discovered URL through the full evaluation pipeline."""
        if not self._app:
            return

        with self._app.app_context():
            try:
                from models.database import db, ScanHistory, Application, JobPosting
                from models.database import _normalize
                from services.job_parser import parse_job_input
                from services.gemini_client import evaluate_job
                from services.dedup import is_duplicate as check_dup

                # Layer 1: URL already scanned?
                if ScanHistory.query.filter_by(url=job_url).first():
                    self.stats.jobs_skipped_duplicate += 1
                    return

                # Parse the job URL
                parsed = parse_job_input(job_url)
                if not parsed.get("success") or not parsed.get("text"):
                    self.stats.errors += 1
                    return

                text = parsed["text"]
                url = parsed.get("url") or job_url

                # Run AI evaluation
                result = evaluate_job(text)
                self.stats.jobs_evaluated += 1

                if not result.get("success"):
                    self.stats.jobs_skipped_filter += 1
                    logger.debug(
                        f"[JobScanner] Filtered: {url} — {result.get('filter_type','?')}"
                    )
                    return

                data = result["data"]
                grade = data.get("grade", "F")
                company = data.get("company", "Unknown")
                role = data.get("role", "Unknown")
                location = data.get("location", "")
                score_raw = data.get("score")
                score = float(score_raw) if score_raw is not None else None

                logger.info(f"[JobScanner] Evaluated: {company} | {role} | Grade {grade}")

                # Dedup check
                dedup = check_dup(url, company, role, location)
                if dedup["is_duplicate"]:
                    self.stats.jobs_skipped_duplicate += 1
                    return

                # Save to database
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

                if url:
                    db.session.add(ScanHistory(
                        url=url,
                        platform="auto_scan",
                        scanned_at=datetime.utcnow(),
                        result="new",
                    ))

                db.session.commit()
                self.stats.jobs_saved += 1

                # Auto-queue grade A or B into the agent
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
                            logger.info(
                                f"[JobScanner] Auto-queued: {company} | {role} | Grade {grade}"
                            )
                    except Exception as e:
                        logger.warning(f"[JobScanner] Could not queue {company}: {e}")

            except Exception as exc:
                logger.warning(f"[JobScanner] Error evaluating {job_url}: {exc}")
                self.stats.errors += 1
                try:
                    db.session.rollback()
                except Exception:
                    pass


# ── Singleton ─────────────────────────────────────────────────────────────────
job_scanner = JobScanner()
