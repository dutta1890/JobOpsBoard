import logging
from fuzzywuzzy import fuzz

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 85


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def is_duplicate(new_company: str, new_role: str, existing_jobs: list) -> dict:
    new_key = normalize(f"{new_company} {new_role}")

    for job in existing_jobs:
        existing_key = normalize(f"{job.company} {job.role}")
        ratio = fuzz.token_sort_ratio(new_key, existing_key)
        if ratio >= SIMILARITY_THRESHOLD:
            logger.info(
                f"Duplicate detected: '{new_key}' ~ '{existing_key}' (score={ratio})"
            )
            return {
                "is_duplicate": True,
                "match": job,
                "similarity": ratio,
            }

    return {"is_duplicate": False, "match": None, "similarity": 0}


def deduplicate_list(jobs: list, key_fn=None) -> list:
    seen = []
    unique = []

    for job in jobs:
        key = key_fn(job) if key_fn else str(job)
        is_dup = False

        for seen_key in seen:
            if fuzz.token_sort_ratio(normalize(key), normalize(seen_key)) >= SIMILARITY_THRESHOLD:
                is_dup = True
                break

        if not is_dup:
            seen.append(key)
            unique.append(job)

    return unique
