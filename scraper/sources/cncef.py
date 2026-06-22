"""
CNCEF Scraper - Chambre Nationale des Conseils Experts Financiers
Scrapes the directory at https://www.cncef.org/annuaire/ via HTML pagination.

HTML structure (verified April 2026):
- Grid container: div.annuaire__grid
- Each card: div.annuaire__item
  - Name: h2.annuaire__item__name
  - Location: p.annuaire__item__place  (e.g. "Paris (75)")
  - Activities: div.annuaire__item__tag-list ul li  (e.g. "Assurance", "Credit", "Patrimoine")
  - Detail link: div.annuaire__item__bottom a.annuaire__item__button
- Pagination: ul.pagination
  - Current: li.current
  - Next: li.next.btn > a
  - Pages numbered 1..411
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

ANNUAIRE_URL = "https://www.cncef.org/annuaire/"

# Map CNCEF activity labels to standard codes
ACTIVITY_MAP = {
    "assurance": "COA",
    "crédit": "IOBSP",
    "credit": "IOBSP",
    "patrimoine": "CIF",
    "expertise financière": "CIF",
    "expertise financiere": "CIF",
    "immobilier": "Immobilier",
    "france m&a": "CIF",
}


def _parse_card(card):
    """Parse a single .annuaire__item card."""
    name_el = card.select_one(".annuaire__item__name")
    if not name_el:
        return None
    name = name_el.get_text(strip=True)
    if not name or len(name) < 2:
        return None

    # Location: "Paris (75)" or "Isere (38)"
    place_el = card.select_one(".annuaire__item__place")
    city = ""
    department = ""
    if place_el:
        place_text = place_el.get_text(strip=True)
        dept_match = re.search(r'\((\d{2,3})\)', place_text)
        if dept_match:
            department = dept_match.group(1)
            city = re.sub(r'\s*\(\d{2,3}\)\s*', '', place_text).strip()
        else:
            city = place_text

    # Activities from tag-list
    raw_activities = []
    for li in card.select(".annuaire__item__tag-list li"):
        text = li.get_text(strip=True)
        if text:
            raw_activities.append(text)

    # Map to standard codes
    activities = []
    specialties = list(raw_activities)
    for act in raw_activities:
        mapped = ACTIVITY_MAP.get(act.lower(), "")
        if mapped and mapped not in activities:
            activities.append(mapped)

    # Detail link
    detail_link = card.select_one("a.annuaire__item__button")
    detail_url = detail_link.get("href", "") if detail_link else ""

    return {
        "name": name,
        "city": city,
        "department": department,
        "activities": activities,
        "specialties": specialties,
        "detail_url": detail_url,
    }


def _parse_detail_page(url):
    """Fetch a CNCEF member detail page.

    The CNCEF cabinet pages expose the real "Siège social" address (street +
    postal + city) and the "Représentant légal" name. They do NOT expose
    cabinet-level phone / email / website (only CNCEF-wide social links).

    Returns a dict that may contain:
      - address_street, postal_code, city
      - director_name, director_role
    """
    try:
        resp = fetch(url, delay=1.0)
        if not resp:
            return {}
        soup = BeautifulSoup(resp.text, "lxml")
        info = {}

        # --- "Siège social" block: typically a row with the label followed by
        # the address in the same container.
        for el in soup.find_all(string=re.compile(r"Si[èe]ge social", re.I)):
            block = el.parent.find_parent("div") or el.parent
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
            # Expected: "Siège social <STREET> <POSTAL> <CITY>"
            m = re.search(
                r"Si[èe]ge social\s+(.+?)\s+(\d{5})\s+([A-ZÀ-Ÿ][A-Za-zÀ-ÿ\-' ]+?)(?:\s+Contacter|$)",
                text)
            if m:
                info["address_street"] = m.group(1).strip()
                info["postal_code"] = m.group(2)
                info["city"] = m.group(3).strip()
                break

        # --- "Représentant légal" block: "NAME [Représentant légal] [- ROLE]"
        for el in soup.find_all(string=re.compile(r"Repr[ée]sentant l[ée]gal", re.I)):
            block = el.parent.find_parent("div") or el.parent
            text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
            # Expected: "NOM Prénom Représentant légal[- ROLE]"
            m = re.search(
                r"^(.{3,60}?)\s*Repr[ée]sentant l[ée]gal(?:\s*-\s*([A-ZÉÈ \-]+))?",
                text)
            if m:
                name = m.group(1).strip()
                role = (m.group(2) or "").strip().title() or "Représentant légal"
                if 3 < len(name) < 60 and not name.lower().startswith(("vos ", "partager")):
                    info["director_name"] = name
                    info["director_role"] = role
                break

        return info
    except Exception as e:
        logger.debug(f"Error parsing detail page {url}: {e}")
        return {}


def _fetch_page(page_num):
    """Fetch a single CNCEF page (for parallel use)."""
    import requests as req
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
               "Accept": "text/html, */*", "Accept-Language": "fr-FR,fr;q=0.9"}
    url = ANNUAIRE_URL if page_num == 1 else f"{ANNUAIRE_URL}page/{page_num}/"
    try:
        r = req.get(url, headers=HEADERS, timeout=30)
        return (page_num, r.text) if r.status_code == 200 else (page_num, None)
    except Exception:
        return (page_num, None)


def scrape_cncef(max_pages=500, enrich_details=False):
    """Scrape the CNCEF directory using parallel page fetching (5 workers)."""
    import concurrent.futures

    logger.info("Starting CNCEF scrape (parallel, 5 workers)...")
    members = []
    seen_names = set()

    # Phase 1: fetch all pages in parallel
    pages = {}
    empty_seen = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        batch_start = 1
        while batch_start <= max_pages and not empty_seen:
            batch_end = min(batch_start + 49, max_pages + 1)
            futures = {pool.submit(_fetch_page, p): p for p in range(batch_start, batch_end)}
            for future in concurrent.futures.as_completed(futures):
                pnum, html = future.result()
                if html:
                    soup = BeautifulSoup(html, "lxml")
                    if soup.select("div.annuaire__item"):
                        pages[pnum] = html
                    else:
                        empty_seen = True
                else:
                    empty_seen = True
            logger.info(f"CNCEF: fetched pages {batch_start}-{batch_end-1}, {len(pages)} with data so far")
            batch_start = batch_end

    logger.info(f"CNCEF: {len(pages)} pages fetched, parsing...")

    # Phase 2: parse sequentially (fast, in-memory)
    for page_num in sorted(pages.keys()):
        soup = BeautifulSoup(pages[page_num], "lxml")
        cards = soup.select("div.annuaire__item")
        page_count = 0
        for card in cards:
            data = _parse_card(card)
            if not data:
                continue
            name = data["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            extra = {}
            if enrich_details and data["detail_url"]:
                extra = _parse_detail_page(data["detail_url"])
            postal_code = extra.get("postal_code", "")
            if not postal_code and data["department"]:
                postal_code = f"{data['department']}000"
            directors = []
            if extra.get("director_name"):
                directors = [{"name": extra["director_name"], "role": "Dirigeant"}]
            member = make_member_dict(
                company_name=name,
                address_street=extra.get("address_street", ""),
                postal_code=postal_code,
                city=extra.get("city") or data["city"],
                phone=extra.get("phone", ""),
                email=extra.get("email", ""),
                website=extra.get("website", ""),
                activities=data["activities"],
                specialties=data["specialties"],
                directors=directors,
                source="cncef",
                source_url=data["detail_url"],
            )
            members.append(member)
            page_count += 1

    logger.info(f"CNCEF: Total = {len(members)} members across {len(pages)} pages")
    return members
