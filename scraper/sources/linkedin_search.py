"""
LinkedIn URL enricher: search DuckDuckGo for the dirigeant's LinkedIn profile.

Adapted from mutuelles-monitor/scraper/enrichment/daf_cio_enricher.py.

Strategy: for each director on a member, search
    "{director_name}" "{company_name}" site:linkedin.com/in
on DuckDuckGo HTML. Validate that the LinkedIn title actually mentions the
target company (avoids returning a homonym from a different firm).

Stores the URL on the director dict:
    member["directors"][i]["linkedin"] = "https://linkedin.com/in/..."
"""
import logging
import re
import time
from urllib.parse import quote_plus, unquote

from bs4 import BeautifulSoup

from .base import fetch_browser

logger = logging.getLogger(__name__)

DDG_URL = "https://html.duckduckgo.com/html/?q="

# LinkedIn URLs sometimes show up as fr.linkedin.com or www.linkedin.com
LINKEDIN_RE = re.compile(r"(?:[\w-]+\.)?linkedin\.com/in/[^&\s\"'?#]+", re.I)


def _ddg_search(query: str, wait_ms: int = 2500):
    """Run a DDG HTML search; return list of LinkedIn results."""
    url = DDG_URL + quote_plus(query)
    try:
        html = fetch_browser(url, wait_ms=wait_ms)
    except Exception as e:
        logger.debug(f"  DDG error for '{query}': {e}")
        return []

    soup = BeautifulSoup(html, "lxml")
    results = []
    for a in soup.select("a.result__a"):
        href = unquote(a.get("href", ""))
        m = LINKEDIN_RE.search(href)
        if not m:
            continue
        link = "https://" + m.group(0)
        title = a.get_text(strip=True)
        results.append({"link": link, "title": title})
    return results


def _title_matches_company(title, company_name):
    """LinkedIn snippet must contain a meaningful token from the company name.

    Why: DDG happily returns a homonym from another firm. Without this check we
    paste the wrong LinkedIn on the wrong cabinet.
    """
    if not title or not company_name:
        return False
    t = title.lower()
    for token in re.split(r"[\s\-/&,()\.]+", company_name.lower()):
        if len(token) >= 4 and token not in {"sarl", "sasu", "eurl", "patrimoine"} and token in t:
            return True
    return False


def _title_looks_like_director(title, director_name):
    """Title should also include at least the dirigeant's surname."""
    if not title or not director_name:
        return False
    surname = director_name.split()[-1].lower() if director_name else ""
    return surname and len(surname) >= 4 and surname in title.lower()


def _short_company_name(company_name):
    """Strip legal suffixes for cleaner search queries."""
    n = company_name or ""
    n = re.sub(r'\([^)]*\)', '', n)
    n = re.sub(r'\b(SARL|SAS|SASU|EURL|SA|SCI|SCP|SELARL)\b', '', n, flags=re.I)
    n = re.sub(r'\s+', ' ', n).strip()
    return n[:50]


def enrich_member(member, delay=2.5):
    """For each director on this member, try to fill linkedin URL.

    Mutates and returns the member dict. Returns count of LinkedIn URLs added.
    """
    company = _short_company_name(member.get("company_name", ""))
    if not company:
        return 0

    directors = member.get("directors") or []
    added = 0
    for d in directors:
        if d.get("linkedin"):
            continue
        if d.get("type") == "personne morale":
            continue
        name = d.get("name", "").strip()
        if not name:
            continue

        # Try 2 query variants, keep the first valid result
        queries = [
            f'"{name}" "{company}" site:linkedin.com/in',
            f'"{name}" {company} site:linkedin.com/in',
        ]
        link = None
        for q in queries:
            results = _ddg_search(q)
            time.sleep(delay)
            for r in results[:5]:
                if not _title_matches_company(r["title"], company):
                    continue
                if not _title_looks_like_director(r["title"], name):
                    continue
                link = r["link"]
                break
            if link:
                break

        if link:
            d["linkedin"] = link
            added += 1

    return added


def batch_enrich(members, max_lookups=None, delay=2.5, log_every=20):
    """Enrich members whose directors lack LinkedIn URLs."""
    candidates = []
    for m in members:
        directors = m.get("directors") or []
        # Only consider physical persons without linkedin
        if any(d.get("type") != "personne morale" and not d.get("linkedin") for d in directors):
            candidates.append(m)

    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"LinkedIn search: {len(candidates)} members to process (delay={delay}s)")

    total_added = 0
    for i, m in enumerate(candidates, 1):
        try:
            added = enrich_member(m, delay=delay)
            total_added += added
        except Exception as e:
            logger.warning(f"  LinkedIn search failed for {m.get('company_name', '?')}: {e}")

        if i % log_every == 0:
            logger.info(f"  LinkedIn {i}/{len(candidates)} (+{total_added} URLs total)")

    logger.info(f"LinkedIn search done: +{total_added} URLs across {len(candidates)} members")
    return members
