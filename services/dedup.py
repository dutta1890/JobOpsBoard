import json
import logging
import re
import hashlib

from fuzzywuzzy import fuzz

logger = logging.getLogger(__name__)

FUZZY_COMPANY_THRESHOLD = 80
FUZZY_ROLE_THRESHOLD = 85


def normalize_company(name: str) -> str:
    name = name.lower().strip()
    suffixes = [
        "pvt ltd", "private limited", "ltd", "limited",
        "inc", "llp", "llc", "gmbh", "bv", "ag",
    ]
    for suffix in suffixes:
        name = re.sub(rf"\s*{re.escape(suffix)}\s*$", "", name)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    return name.strip()


def normalize_role(title: str) -> str:
    title = title.lower().strip()
    replacements = {
        "sr.": "senior",
        "sr ": "senior ",
        "mgr": "manager",
        "mktg": "marketing",
        "perf": "performance",
        "exec": "executive",
        "dir": "director",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    title = re.sub(r"[^a-z0-9\s]", "", title)
    return title.strip()


def create_job_hash(company: str, role: str, location: str) -> str:
    normalized = (
        f"{normalize_company(company)}_{normalize_role(role)}"
        f"_{location.lower().strip()}"
    )
    return hashlib.sha256(normalized.encode()).hexdigest()[:64]


def check_exact_url_duplicate(url: str) -> bool:
    """Layer 1: exact URL match in ScanHistory."""
    if not url:
        return False
    from models.database import ScanHistory
    return ScanHistory.query.filter_by(url=url).first() is not None


def check_hash_duplicate(company: str, role: str, location: str, url: str = None):
    """Layer 2: normalised SHA-256 hash match in JobPosting.

    Returns (is_dup: bool, existing: JobPosting|None, job_hash: str).
    Side-effect: increments times_seen and appends the URL to duplicate_urls
    when a match is found.
    """
    from models.database import JobPosting, db

    job_hash = create_job_hash(company, role, location)
    existing = JobPosting.query.filter_by(job_hash=job_hash).first()

    if existing:
        existing.times_seen += 1
        if url:
            existing.add_duplicate_url(url)
        db.session.commit()
        return True, existing, job_hash

    return False, None, job_hash


def check_fuzzy_duplicate(company: str, role: str):
    """Layer 3: fuzzy match against all postings for a similar company prefix.

    Returns a list of match dicts sorted by role_similarity desc.
    """
    from models.database import JobPosting

    company_norm = normalize_company(company)
    role_norm = normalize_role(role)

    prefix = company_norm[:5] if len(company_norm) >= 5 else company_norm
    candidates = JobPosting.query.filter(
        JobPosting.company_normalized.like(f"%{prefix}%")
    ).all()

    matches = []
    for posting in candidates:
        company_score = fuzz.ratio(company_norm, posting.company_normalized or "")
        role_score = fuzz.ratio(role_norm, posting.role_normalized or "")

        if company_score > FUZZY_COMPANY_THRESHOLD and role_score > FUZZY_ROLE_THRESHOLD:
            matches.append(
                {
                    "posting": posting,
                    "company_similarity": company_score,
                    "role_similarity": role_score,
                }
            )

    matches.sort(key=lambda m: m["role_similarity"], reverse=True)
    return matches


def is_duplicate(url: str, company: str, role: str, location: str) -> dict:
    """Run all 3 deduplication layers.

    Returns a result dict:
      - is_duplicate (bool)
      - duplicate_type: 'exact_url' | 'hash_match' | 'fuzzy_match' | None
      - existing_posting: JobPosting | None
      - job_hash: str
      - fuzzy_matches: list (only populated for soft near-matches, not hard dups)
    """
    result = {
        "is_duplicate": False,
        "duplicate_type": None,
        "existing_posting": None,
        "job_hash": None,
        "fuzzy_matches": [],
    }

    # Layer 1 — exact URL
    if check_exact_url_duplicate(url):
        result["is_duplicate"] = True
        result["duplicate_type"] = "exact_url"
        logger.info(f"[dedup] Layer-1 exact URL match: {url}")
        return result

    # Layer 2 — hash
    is_hash_dup, existing, job_hash = check_hash_duplicate(company, role, location, url)
    result["job_hash"] = job_hash

    if is_hash_dup:
        result["is_duplicate"] = True
        result["duplicate_type"] = "hash_match"
        result["existing_posting"] = existing
        logger.info(
            f"[dedup] Layer-2 hash match: {company} / {role} "
            f"(seen {existing.times_seen}x)"
        )
        return result

    # Layer 3 — fuzzy
    fuzzy_matches = check_fuzzy_duplicate(company, role)
    if fuzzy_matches:
        top = fuzzy_matches[0]
        if top["role_similarity"] > FUZZY_ROLE_THRESHOLD:
            result["is_duplicate"] = True
            result["duplicate_type"] = "fuzzy_match"
            result["existing_posting"] = top["posting"]
            logger.info(
                f"[dedup] Layer-3 fuzzy match: '{company}/{role}' ~ "
                f"'{top['posting'].company_normalized}/{top['posting'].role_normalized}' "
                f"({top['role_similarity']}%)"
            )
        else:
            result["fuzzy_matches"] = fuzzy_matches

    return result


def deduplicate_list(jobs: list, key_fn=None) -> list:
    """Remove near-duplicates from an in-memory list (no DB)."""
    seen = []
    unique = []

    for job in jobs:
        key = key_fn(job) if key_fn else str(job)
        key_norm = " ".join(key.lower().split())
        is_dup = any(
            fuzz.token_sort_ratio(key_norm, " ".join(s.lower().split())) >= FUZZY_ROLE_THRESHOLD
            for s in seen
        )
        if not is_dup:
            seen.append(key)
            unique.append(job)

    return unique
