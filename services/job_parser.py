import logging
import re
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def extract_from_url(url: str) -> dict:
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = "\n".join(lines)

        title = soup.find("title")
        page_title = title.get_text(strip=True) if title else ""

        return {
            "success": True,
            "text": cleaned[:8000],
            "title": page_title,
            "url": url,
        }
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching URL: {url}")
        return {"success": False, "error": "Request timed out"}
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching {url}: {e}")
        return {"success": False, "error": f"HTTP error: {e.response.status_code}"}
    except Exception as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        return {"success": False, "error": str(e)}


def parse_job_input(raw_input: str) -> dict:
    raw_input = raw_input.strip()

    url_pattern = re.compile(
        r"https?://[^\s]+"
    )
    urls = url_pattern.findall(raw_input)

    if urls:
        url = urls[0]
        result = extract_from_url(url)
        if result["success"]:
            remaining = raw_input.replace(url, "").strip()
            if remaining:
                result["text"] = remaining + "\n\n" + result["text"]
        return result
    else:
        return {
            "success": True,
            "text": raw_input,
            "title": "",
            "url": None,
        }


def extract_company_role(text: str) -> dict:
    lines = text.splitlines()
    company = ""
    role = ""

    for line in lines[:20]:
        lower = line.lower()
        if any(kw in lower for kw in ["at ", "company:", "employer:", "organization:"]):
            company = line.strip()[:100]
            break

    for line in lines[:10]:
        lower = line.lower()
        if any(kw in lower for kw in [
            "manager", "director", "specialist", "analyst",
            "lead", "head of", "vp ", "engineer", "marketer"
        ]):
            role = line.strip()[:100]
            break

    return {"company": company or "Unknown", "role": role or "Unknown"}
