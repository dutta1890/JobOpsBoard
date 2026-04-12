"""
legitimacy_checker.py — URL and job posting legitimacy verification for Career-Ops.

Protects against phishing, scams, and malicious sites before visiting or applying.
Checks are layered: trusted-list fast-path → SSL → domain age → TLD → typosquatting → IP.
"""

import logging
import re
import socket
import ssl
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Trusted domain lists ───────────────────────────────────────────────────────

TRUSTED_JOB_PORTALS = {
    # Global
    "linkedin.com", "indeed.com", "glassdoor.com", "naukri.com",
    "monster.com", "shine.com", "instahyre.com", "cutshort.io",
    "greenhouse.io", "lever.co", "workday.com", "ashbyhq.com",
    "angel.co", "wellfound.com", "jobs.lever.co", "boards.greenhouse.io",
    "apply.workable.com", "jobvite.com", "icims.com", "myworkdayjobs.com",
    "smartrecruiters.com", "recruitee.com", "breezy.hr",
    # India
    "foundit.in", "iimjobs.com", "hirist.com",
    # Singapore
    "jobstreet.com.sg", "mycareersfuture.gov.sg", "jobsdb.com",
    # UAE
    "bayt.com", "gulftalent.com", "naukrigulf.com", "dubizzle.com",
    # Europe
    "stepstone.de", "xing.com", "indeed.de", "indeed.nl",
    "jobindex.dk", "landing.jobs",
    # Google Forms
    "docs.google.com", "forms.gle",
}

TRUSTED_COMPANY_DOMAINS = {
    # Big Tech
    "google.com", "meta.com", "amazon.com", "microsoft.com", "apple.com",
    "linkedin.com", "twitter.com", "netflix.com", "uber.com", "airbnb.com",
    # AI
    "anthropic.com", "openai.com", "deepmind.com", "cohere.com",
    # Indian Tech
    "flipkart.com", "swiggy.com", "zomato.com", "razorpay.com",
    "freshworks.com", "zoho.com", "phonepe.com", "cred.club",
    "nykaa.com", "mamaearth.in", "hdfcbank.com", "icicibank.com",
    # UAE
    "noon.com", "careem.com", "talabat.com",
}

SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",          # Free / scam-heavy
    ".xyz", ".top", ".work", ".click",           # Cheap bulk domains
    ".loan", ".racing", ".win", ".download",
}

KNOWN_BRANDS = [
    "linkedin", "indeed", "glassdoor", "google", "facebook",
    "amazon", "microsoft", "apple", "naukri", "flipkart",
    "swiggy", "zomato", "phonepe",
]

SCAM_PATTERNS = [
    r"work.*from.*home.*earn",
    r"easy.*money",
    r"no.*experience.*required.*\$\d{4,}",
    r"crypto.*trading.*job",
    r"forex.*trading.*job",
    r"\bmlm\b",
    r"pyramid.*scheme",
    r"guaranteed.*income",
    r"make.*\$\d{3,}.*per.*day",
]


class LegitimacyChecker:
    """Multi-layer legitimacy verification for job URLs and postings."""

    def check_url(self, url: str) -> dict:
        """Run all legitimacy checks on *url*.

        Returns:
            {
                legitimate: bool,
                tier: 'trusted' | 'verified' | 'unknown' | 'suspicious' | 'dangerous',
                score: int (0–100),
                warnings: list[str],
                blocks: list[str],
                checks: dict,
            }
        """
        result: dict = {
            "legitimate": True,
            "tier": "unknown",
            "score": 50,
            "warnings": [],
            "blocks": [],
            "checks": {},
        }

        if not url:
            result["tier"] = "unknown"
            return result

        # ── Parse ──────────────────────────────────────────────────────────────
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            domain = netloc.removeprefix("www.")
        except Exception as exc:
            result["legitimate"] = False
            result["tier"] = "dangerous"
            result["blocks"].append(f"Invalid URL: {exc}")
            return result

        # ── Fast-path: trusted lists ───────────────────────────────────────────
        if self._is_trusted_portal(domain):
            result["tier"] = "trusted"
            result["score"] = 95
            result["checks"]["trusted_portal"] = True
            return result

        if self._is_trusted_company(domain):
            result["tier"] = "trusted"
            result["score"] = 90
            result["checks"]["trusted_company"] = True
            return result

        # ── Check: HTTPS ───────────────────────────────────────────────────────
        if parsed.scheme != "https":
            result["warnings"].append("URL does not use HTTPS")
            result["score"] -= 15

        # ── Check: IP-based URL ───────────────────────────────────────────────
        if self._is_ip_based(netloc):
            result["blocks"].append("URL uses a raw IP address instead of a domain name")
            result["score"] -= 50
            result["checks"]["ip_based"] = True

        # ── Check: SSL certificate ────────────────────────────────────────────
        ssl_check = self._check_ssl(domain)
        result["checks"]["ssl"] = ssl_check
        if not ssl_check.get("valid"):
            result["warnings"].append("No valid SSL certificate")
            result["score"] -= 20

        # ── Check: Domain age (via whois) ─────────────────────────────────────
        age_check = self._check_domain_age(domain)
        result["checks"]["domain_age"] = age_check
        days = age_check.get("days")
        if days is not None and days < 180:
            result["warnings"].append(f"Domain is only {days} days old (registered recently)")
            result["score"] -= 15

        # ── Check: Suspicious TLD ─────────────────────────────────────────────
        if self._has_suspicious_tld(domain):
            result["warnings"].append(f"Suspicious top-level domain ({domain.rsplit('.', 1)[-1]})")
            result["score"] -= 10
            result["checks"]["suspicious_tld"] = True

        # ── Check: Typosquatting ──────────────────────────────────────────────
        typo = self._check_typosquatting(domain)
        result["checks"]["typosquatting"] = typo
        if typo["is_typosquat"]:
            result["blocks"].append(
                f"Possible typosquatting of '{typo['similar_to']}' — verify this is the real site"
            )
            result["score"] -= 40

        # ── Final verdict ─────────────────────────────────────────────────────
        if result["blocks"]:
            result["legitimate"] = False
            result["tier"] = "dangerous"
        elif result["score"] >= 80:
            result["tier"] = "verified"
        elif result["score"] >= 50:
            result["tier"] = "unknown"
        else:
            result["tier"] = "suspicious"
            result["legitimate"] = False

        return result

    def check_jd_text(self, jd_text: str) -> dict:
        """Scan job description text for scam patterns.

        Returns:
            { is_scam: bool, patterns_found: list[str] }
        """
        if not jd_text:
            return {"is_scam": False, "patterns_found": []}

        text_lower = jd_text.lower()
        found = []
        for pattern in SCAM_PATTERNS:
            if re.search(pattern, text_lower):
                found.append(pattern)

        return {"is_scam": bool(found), "patterns_found": found}

    # ── Internal checks ────────────────────────────────────────────────────────

    def _is_trusted_portal(self, domain: str) -> bool:
        return any(
            domain == p or domain.endswith("." + p) for p in TRUSTED_JOB_PORTALS
        )

    def _is_trusted_company(self, domain: str) -> bool:
        return any(
            domain == c or domain.endswith("." + c) for c in TRUSTED_COMPANY_DOMAINS
        )

    def _check_ssl(self, domain: str) -> dict:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((domain, 443), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    return {
                        "valid": True,
                        "issuer": dict(x[0] for x in cert.get("issuer", [])),
                        "expires": cert.get("notAfter", ""),
                    }
        except Exception as exc:
            return {"valid": False, "error": str(exc)}

    def _check_domain_age(self, domain: str) -> dict:
        try:
            import whois
            w = whois.whois(domain)
            creation = w.creation_date
            if isinstance(creation, list):
                creation = creation[0]
            if creation and isinstance(creation, datetime):
                age_days = (datetime.now() - creation).days
                return {"days": age_days, "created": str(creation)}
        except Exception as exc:
            logger.debug(f"WHOIS lookup failed for {domain}: {exc}")
        return {"days": None, "error": "Could not determine domain age"}

    def _has_suspicious_tld(self, domain: str) -> bool:
        return any(domain.endswith(tld) for tld in SUSPICIOUS_TLDS)

    def _check_typosquatting(self, domain: str) -> dict:
        domain_base = domain.split(".")[0]
        for brand in KNOWN_BRANDS:
            dist = self._levenshtein(domain_base, brand)
            if dist <= 2 and domain_base != brand:
                return {"is_typosquat": True, "similar_to": brand, "distance": dist}
        return {"is_typosquat": False}

    def _is_ip_based(self, netloc: str) -> bool:
        # Strip port if present
        host = netloc.split(":")[0]
        return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host))

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            s1, s2 = s2, s1
        if not s2:
            return len(s1)
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
            prev = curr
        return prev[-1]


# ── Singleton ──────────────────────────────────────────────────────────────────
legitimacy_checker = LegitimacyChecker()
