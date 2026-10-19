"""schema.org JobPosting from a job page (iCIMS classic and other pages that embed JSON-LD for search
engines). Only the first JobPosting object is read; anything else on the page is ignored."""
import json
import re
from datetime import datetime

from .common import iso_utc

_LD = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _nodes(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("@graph") or [data]
    return []


def job_posting(page: str) -> dict | None:
    for block in _LD.findall(page or ""):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _nodes(data):
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


_PLACEHOLDERS = {"UNAVAILABLE", "N/A", "NA", "NONE", "NULL"}          # iCIMS fills an unknown region with 'UNAVAILABLE'


def _addresses(posting: dict) -> list[dict]:
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    return [p.get("address") for p in places if isinstance(p, dict) and isinstance(p.get("address"), dict)]


def location(posting: dict) -> tuple[str, str | None]:
    """'Saint Louis, MO, US; Denver, CO, US' and the country when every place shares one."""
    texts, countries = [], set()
    for addr in _addresses(posting):
        country = addr.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        parts = [str(p).strip() for p in (addr.get("addressLocality"), addr.get("addressRegion"), country) if p]
        texts.append(", ".join(p for p in parts if p and p.upper() not in _PLACEHOLDERS))
        if country:
            countries.add(str(country).strip())
    return "; ".join(t for t in texts if t), (countries.pop() if len(countries) == 1 else None)


def posted(posting: dict) -> str | None:
    text = posting.get("datePosted")
    if not isinstance(text, str) or not text:
        return None
    try:
        return iso_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def employment(posting: dict) -> str | None:
    kind = posting.get("employmentType")
    return ", ".join(kind) if isinstance(kind, list) else kind


def organization(posting: dict) -> str | None:
    org = posting.get("hiringOrganization")
    return org.get("name") if isinstance(org, dict) else org if isinstance(org, str) else None
