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
    """Fetch a member detail page for phone/email/website/directors."""
    try:
        resp = fetch(url, delay=1.0)
        if not resp:
            return {}
        soup = BeautifulSoup(resp.text, "lxml")
        info = {}

        # Phone
        phone_el = soup.find("a", href=re.compile(r"^tel:"))
        if phone_el:
            info["phone"] = phone_el.get("href", "").replace("tel:", "").strip()

        # Email
        email_el = soup.find("a", href=re.compile(r"^mailto:"))
        if email_el:
            info["email"] = email_el.get("href", "").replace("mailto:", "").strip()

        # Website
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("http") and "cncef.org" not in href:
                text = a.get_text(strip=True).lower()
                if any(w in text for w in ["site", "web", "www"]) or any(w in href for w in ["www.", ".fr", ".com"]):
                    info["website"] = href
                    break

        # Address
        text = soup.get_text(" ", strip=True)
        pc_match = re.search(r'(\d{5})\s+([A-Z][a-zA-ZÀ-ÿ\s\-]+)', text)
        if pc_match:
            info["postal_code"] = pc_match.group(1)
            info["city"] = pc_match.group(2).strip()

        street_match = re.search(r'(\d+[\s,]+(?:rue|avenue|boulevard|place|chemin|impasse|allée|cours)\s+[^,\n]+)', text, re.I)
        if street_match:
            info["address_street"] = street_match.group(1).strip()

        # Director
        dirigeant_match = re.search(
            r'(?:Dirigeant|Gérant|Président|Contact)\s*:?\s*(.+?)(?:\n|$|Téléphone|Email|Adresse)',
            text, re.I
        )
        if dirigeant_match:
            d_name = dirigeant_match.group(1).strip()
            d_name = re.sub(r'\s+', ' ', d_name)
            if 3 < len(d_name) < 60:
                info["director_name"] = d_name

        return info
    except Exception as e:
        logger.debug(f"Error parsing detail page {url}: {e}")
        return {}


def scrape_cncef(max_pages=500, enrich_details=False):
    """
    Scrape the CNCEF directory by paginating through all pages.

    Args:
        max_pages: Maximum pages to scrape (safety limit, total is ~411)
        enrich_details: If True, follow detail links for phone/email (much slower)

    Returns:
        List of normalized member dicts
    """
    logger.info("Starting CNCEF scrape...")
    members = []
    seen_names = set()
    page_num = 1

    while page_num <= max_pages:
        url = ANNUAIRE_URL if page_num == 1 else f"{ANNUAIRE_URL}page/{page_num}/"
        logger.info(f"CNCEF: page {page_num}...")

        try:
            resp = fetch(url, delay=1.0)
            if not resp:
                logger.warning(f"CNCEF: No response for page {page_num}, stopping")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # Find member cards using the correct CSS selector
            cards = soup.select("div.annuaire__item")
            if not cards:
                logger.info(f"CNCEF: No cards on page {page_num}, stopping")
                break

            page_count = 0
            for card in cards:
                data = _parse_card(card)
                if not data:
                    continue

                name = data["name"]
                if name in seen_names:
                    continue
                seen_names.add(name)

                # Enrich from detail page if requested
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

            logger.info(f"CNCEF: page {page_num} -> {page_count} new members")

            # Check pagination: is there a next page?
            pagination = soup.select_one("ul.pagination")
            if not pagination:
                break

            next_btn = pagination.select_one("li.next a")
            if not next_btn:
                # No "Suivant" button = last page
                break

            page_num += 1

        except Exception as e:
            logger.error(f"CNCEF: Error on page {page_num}: {e}")
            page_num += 1
            continue

    logger.info(f"CNCEF: Total = {len(members)} members across {page_num} pages")
    return members
