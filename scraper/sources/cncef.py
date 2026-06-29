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


_thread_local = None  # lazily-initialised threading.local for per-thread Sessions


def _get_session():
    """Return a thread-local requests.Session with HTTP keep-alive + urllib3 retries.

    Reusing one connection per worker (keep-alive) is far faster and more
    reliable than opening a fresh TCP+TLS connection for every page, which is
    what was making the CNCEF scrape crawl and drop connections.
    """
    global _thread_local
    import threading
    import requests as req
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        from requests.packages.urllib3.util.retry import Retry

    if _thread_local is None:
        _thread_local = threading.local()
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = req.Session()
        retry = Retry(total=2, connect=2, read=2, backoff_factor=0.3,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET"])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Connection": "keep-alive",
        })
        _thread_local.session = sess
    return sess


def _fetch_page(page_num):
    """Fetch a single CNCEF page using a thread-local keep-alive session.

    Returns (page_num, status, html):
      - 'ok'   : HTTP 200 with member cards -> html is the page text
      - 'end'  : HTTP 404 / 200 with no cards -> genuine end of directory
      - 'fail' : error after the adapter's built-in retries -> retry later
    """
    url = ANNUAIRE_URL if page_num == 1 else f"{ANNUAIRE_URL}page/{page_num}/"
    try:
        r = _get_session().get(url, timeout=15)
        if r.status_code == 200:
            if "annuaire__item" in r.text:
                return (page_num, "ok", r.text)
            return (page_num, "end", "")
        if r.status_code == 404:
            return (page_num, "end", "")
    except Exception:
        pass
    return (page_num, "fail", "")


def scrape_cncef(max_pages=450, enrich_details=False):
    """Scrape the CNCEF directory SEQUENTIALLY over one keep-alive connection.

    The CNCEF server drops concurrent connections, which made every parallel
    version unreliable (it would stop after a handful of pages). Sequential
    fetching over a single keep-alive session is the only reliable approach:
    slower (~30-45 min for ~410 pages) but it actually gets all ~4800 members.

    Robustness:
      - each page is tried a few times; a network 'fail' is retried, never
        mistaken for the end of the directory
      - the scrape stops ONLY on a genuine empty page (HTTP 404 / 200-no-cards)
      - a long run of consecutive hard failures (server down) aborts safely
    """
    logger.info("Starting CNCEF scrape (sequential, keep-alive)...")
    members = []
    seen_names = set()

    pages = {}              # page_num -> html
    consecutive_fail = 0    # safety brake if the server goes down mid-scrape

    import time
    for page_num in range(1, max_pages + 1):
        # Try this page up to 2 times (the session adapter already retries x2
        # internally, so that's ~4 real attempts) before giving up on it.
        status, html = "fail", ""
        for attempt in range(2):
            _, status, html = _fetch_page(page_num)
            if status in ("ok", "end"):
                break
        if status == "ok":
            pages[page_num] = html
            consecutive_fail = 0
            time.sleep(0.2)  # politeness delay - a steady cadence drops fewer connections
        elif status == "end":
            logger.info(f"CNCEF: reached end of directory at page {page_num}")
            break
        else:  # fail after all attempts -> skip page, keep going (additive merge)
            consecutive_fail += 1
            logger.warning(f"CNCEF: page {page_num} failed after retries - skipped")
            if consecutive_fail >= 10:
                logger.error("CNCEF: 10 consecutive failures - aborting (server down?)")
                break
        if page_num % 25 == 0:
            logger.info(f"CNCEF: {page_num} pages done, {len(pages)} fetched")

    logger.info(f"CNCEF: {len(pages)} pages fetched, parsing...")

    # Phase 3: parse sequentially (fast, in-memory)
    for page_num in sorted(pages.keys()):
        soup = BeautifulSoup(pages[page_num], "lxml")
        cards = soup.select("div.annuaire__item")
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

    logger.info(f"CNCEF: Total = {len(members)} members across {len(pages)} pages")
    return members
