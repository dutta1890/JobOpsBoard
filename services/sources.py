import json
import logging
import os

logger = logging.getLogger(__name__)

_SOURCES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "job_sources.json")
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_SOURCES_PATH) as f:
                _cache = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load job_sources.json: {e}")
            _cache = {}
    return _cache


_REGION_ALIASES = {
    "india": "india",
    "in": "india",
    "mumbai": "india",
    "pune": "india",
    "sg": "singapore",
    "singapore": "singapore",
    "uae": "uae",
    "dubai": "uae",
    "abu dhabi": "uae",
    "de": "germany",
    "germany": "germany",
    "nl": "netherlands",
    "netherlands": "netherlands",
    "se": "sweden",
    "sweden": "sweden",
    "pt": "portugal",
    "portugal": "portugal",
    "dk": "denmark",
    "denmark": "denmark",
    "europe": ["germany", "netherlands", "sweden", "portugal", "denmark"],
}


def _resolve_regions(region: str) -> list[str]:
    key = region.lower().strip()
    resolved = _REGION_ALIASES.get(key, key)
    if isinstance(resolved, list):
        return resolved
    return [resolved]


def get_sources_by_region(region: str) -> list[dict]:
    data = _load()
    regions = _resolve_regions(region)
    results = []

    portals = data.get("job_portals", {})
    for r in regions:
        for entry in portals.get(r, []):
            results.append({**entry, "category": "job_portal", "region": r})

    regional_categories = [
        ("boutique_agencies", "boutique_agency"),
        ("d2c_brands", "d2c_brand"),
        ("ecommerce", "ecommerce"),
        ("quick_commerce", "quick_commerce"),
        ("fintech", "fintech"),
    ]
    for cat_key, cat_label in regional_categories:
        cat = data.get(cat_key, {})
        if isinstance(cat, dict):
            for r in regions:
                for entry in cat.get(r, []):
                    results.append({**entry, "category": cat_label, "region": r})

    if any(r == "uae" for r in regions):
        for entry in data.get("uae_conglomerates", []):
            results.append({**entry, "category": "uae_conglomerate", "region": "uae"})

    europe_regions = {"germany", "netherlands", "sweden", "portugal", "denmark"}
    if any(r in europe_regions for r in regions):
        ecom = data.get("ecommerce", {})
        for entry in ecom.get("europe", []):
            results.append({**entry, "category": "ecommerce", "region": "europe"})
        qcom = data.get("quick_commerce", {})
        for entry in qcom.get("europe", []):
            results.append({**entry, "category": "quick_commerce", "region": "europe"})

    return results


def get_all_company_careers() -> list[dict]:
    data = _load()
    results = []

    for entry in data.get("tech_platforms", []):
        results.append({**entry, "category": "tech_platform"})

    for entry in data.get("fmcg_cpg", []):
        results.append({**entry, "category": "fmcg_cpg"})

    for entry in data.get("consulting", []):
        results.append({**entry, "category": "consulting"})

    for entry in data.get("b2b_saas", []):
        results.append({**entry, "category": "b2b_saas"})

    for entry in data.get("edtech", []):
        results.append({**entry, "category": "edtech"})

    for entry in data.get("travel_hospitality", []):
        results.append({**entry, "category": "travel_hospitality"})

    for entry in data.get("uae_conglomerates", []):
        results.append({**entry, "category": "uae_conglomerate"})

    for network in data.get("agency_networks", {}).get("global", []):
        results.append({**network, "category": "agency_network"})

    for cat_key in ("boutique_agencies", "d2c_brands", "ecommerce", "quick_commerce", "fintech"):
        cat = data.get(cat_key, {})
        if isinstance(cat, dict):
            for region_entries in cat.values():
                for entry in region_entries:
                    results.append({**entry, "category": cat_key})
        elif isinstance(cat, list):
            for entry in cat:
                results.append({**entry, "category": cat_key})

    return results


def get_sources_for_scanning(region: str, category: str | None = None) -> list[dict]:
    data = _load()
    sources = []

    regional = get_sources_by_region(region)
    global_companies = get_all_company_careers()

    all_sources = regional + global_companies

    if category:
        cat_lower = category.lower().strip()
        all_sources = [s for s in all_sources if cat_lower in s.get("category", "").lower()]

    seen_careers = set()
    for s in all_sources:
        key = s.get("careers") or s.get("url") or s.get("name")
        if key and key not in seen_careers:
            seen_careers.add(key)
            sources.append(s)

    return sources


def get_all_job_portals() -> dict:
    return _load().get("job_portals", {})


def get_reddit_communities() -> list[dict]:
    return _load().get("reddit_communities", [])


def get_vc_portfolio_sources() -> list[dict]:
    return _load().get("vc_portfolio_jobs", [])


def count_sources() -> dict:
    data = _load()
    total = 0
    breakdown = {}

    for region, portals in data.get("job_portals", {}).items():
        n = len(portals)
        breakdown[f"portals_{region}"] = n
        total += n

    list_keys = ["tech_platforms", "fmcg_cpg", "consulting", "b2b_saas",
                 "edtech", "travel_hospitality", "uae_conglomerates",
                 "vc_portfolio_jobs", "reddit_communities"]
    for key in list_keys:
        n = len(data.get(key, []))
        breakdown[key] = n
        total += n

    regional_keys = ["boutique_agencies", "d2c_brands", "ecommerce",
                     "quick_commerce", "fintech"]
    for key in regional_keys:
        cat = data.get(key, {})
        if isinstance(cat, dict):
            n = sum(len(v) for v in cat.values())
        else:
            n = len(cat)
        breakdown[key] = n
        total += n

    for network in data.get("agency_networks", {}).get("global", []):
        total += 1 + len(network.get("agencies", []))

    breakdown["total"] = total
    return breakdown
