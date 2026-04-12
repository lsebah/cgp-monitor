"""
CNCEF Scraper - Chambre Nationale des Conseils Experts Financiers
Scrapes the directory at https://www.cncef.org/annuaire/ via AJAX pagination.
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

ANNUAIRE_URL = "https://www.cncef.org/annuaire/"
AJAX_URL = "https://www.cncef.org/wp-admin/admin-ajax.php"


def _get_page_html(page_url):
    """Fetch a page of the directory and return the HTML."""
    resp = fetch(page_url, delay=0.8)
    if not resp:
        return None
    return resp.text


def _parse_detail_page(url):
    """Fetch a member detail page and extract additional info."""
    try:
        resp = fetch(url, delay=0.8)
        if not resp:
            return {}
        soup = BeautifulSoup(resp.text, "lxml")
        info = {}

        # Look for phone
        phone_el = soup.find("a", href=re.compile(r"^tel:"))
        if phone_el:
            info["phone"] = phone_el.get("href", "").replace("tel:", "").strip()

        # Look for email
        email_el = soup.find("a", href=re.compile(r"^mailto:"))
        if email_el:
            info["email"] = email_el.get("href", "").replace("mailto:", "").strip()

        # Look for website
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("http") and "cncef.org" not in href:
                text = a.get_text(strip=True).lower()
                if "site" in text or "web" in text or "www" in text:
                    info["website"] = href
                    break

        # Look for address in text
        addr_section = soup.find("div", class_=re.compile(r"adresse|address|coordonnees", re.I))
        if addr_section:
            text = addr_section.get_text("\n", strip=True)
            info["address_text"] = text

        # Look for directors/dirigeants
        dirigeant_section = soup.find(string=re.compile(r"Dirigeant|Responsable|Contact", re.I))
        if dirigeant_section:
            parent = dirigeant_section.find_parent()
            if parent:
                next_el = parent.find_next_sibling()
                if next_el:
                    info["director_name"] = next_el.get_text(strip=True)

        return info
    except Exception as e:
        logger.debug(f"Error parsing detail page {url}: {e}")
        return {}


def _parse_member_card(card):
    """Parse a single member card from the directory listing."""
    member_data = {}

    # Company name - usually in a heading or strong link
    name_el = card.find(["h2", "h3", "h4", "strong"])
    if not name_el:
        name_link = card.find("a")
        if name_link:
            name_el = name_link
    if name_el:
        member_data["company_name"] = name_el.get_text(strip=True)

    # Detail URL
    link = card.find("a", href=True)
    if link:
        href = link.get("href", "")
        if href and "cncef.org" in href:
            member_data["detail_url"] = href

    # Location - look for department code pattern like (75) or (13)
    card_text = card.get_text(" ", strip=True)
    dept_match = re.search(r'\((\d{2,3})\)', card_text)
    if dept_match:
        member_data["department"] = dept_match.group(1)

    # City
    location_el = card.find(class_=re.compile(r"location|ville|city|lieu", re.I))
    if location_el:
        loc_text = location_el.get_text(strip=True)
        # Remove department code in parens
        loc_text = re.sub(r'\(\d{2,3}\)', '', loc_text).strip()
        member_data["city"] = loc_text

    # Activities/domains - look for badge-like elements
    activities = []
    for badge in card.find_all(class_=re.compile(r"badge|tag|domain|activit", re.I)):
        text = badge.get_text(strip=True)
        if text and len(text) < 50:
            activities.append(text)
    # Also check list items
    for li in card.find_all("li"):
        text = li.get_text(strip=True)
        if text and len(text) < 50:
            activities.append(text)
    member_data["activities"] = activities

    return member_data


def scrape_cncef(max_pages=500, enrich_details=False):
    """
    Scrape the CNCEF directory.

    Args:
        max_pages: Maximum pages to scrape (safety limit)
        enrich_details: If True, follow detail links for phone/email (much slower)

    Returns:
        List of normalized member dicts
    """
    logger.info("Starting CNCEF scrape...")
    members = []
    seen_names = set()

    # Start with the first page to understand the structure
    page_num = 1
    base_url = ANNUAIRE_URL

    while page_num <= max_pages:
        if page_num == 1:
            url = base_url
        else:
            url = f"{base_url}page/{page_num}/"

        logger.info(f"CNCEF: Scraping page {page_num}...")

        try:
            html = _get_page_html(url)
            if not html:
                logger.warning(f"CNCEF: No response for page {page_num}, stopping")
                break

            soup = BeautifulSoup(html, "lxml")

            # Find member cards - try common container patterns
            cards = soup.find_all("article")
            if not cards:
                cards = soup.find_all("div", class_=re.compile(r"member|adherent|result|card|annuaire", re.I))
            if not cards:
                # Try finding a results container first
                container = soup.find("div", class_=re.compile(r"results|listing|annuaire|archive", re.I))
                if container:
                    cards = container.find_all("div", recursive=False)

            if not cards:
                logger.info(f"CNCEF: No cards found on page {page_num}, stopping")
                break

            page_count = 0
            for card in cards:
                data = _parse_member_card(card)
                name = data.get("company_name", "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                # Build member dict
                city = data.get("city", "")
                dept = data.get("department", "")
                postal_code = f"{dept}000" if dept and not city else ""

                activities = []
                for act in data.get("activities", []):
                    act_lower = act.lower()
                    if "patrimoine" in act_lower or "cif" in act_lower:
                        activities.append("CIF")
                    elif "assurance" in act_lower:
                        activities.append("COA")
                    elif "credit" in act_lower or "iobsp" in act_lower:
                        activities.append("IOBSP")
                    elif "immobilier" in act_lower:
                        activities.append("Immobilier")
                    else:
                        activities.append(act)

                extra_info = {}
                if enrich_details and data.get("detail_url"):
                    extra_info = _parse_detail_page(data["detail_url"])

                directors = []
                if extra_info.get("director_name"):
                    directors = [{"name": extra_info["director_name"], "role": "Dirigeant"}]

                member = make_member_dict(
                    company_name=name,
                    postal_code=postal_code,
                    city=city,
                    phone=extra_info.get("phone", ""),
                    email=extra_info.get("email", ""),
                    website=extra_info.get("website", ""),
                    activities=list(set(activities)),
                    specialties=data.get("activities", []),
                    directors=directors,
                    source="cncef",
                    source_url=data.get("detail_url", ""),
                )
                members.append(member)
                page_count += 1

            logger.info(f"CNCEF: Page {page_num} -> {page_count} members")

            # Check if there's a next page
            next_link = soup.find("a", class_=re.compile(r"next", re.I))
            if not next_link:
                # Also check for pagination with page numbers
                pagination = soup.find(class_=re.compile(r"pagination|nav-links", re.I))
                if pagination:
                    current = pagination.find(class_=re.compile(r"current|active", re.I))
                    if current:
                        next_page = current.find_next_sibling("a")
                        if not next_page:
                            break
                    else:
                        break
                else:
                    break

            page_num += 1

        except Exception as e:
            logger.error(f"CNCEF: Error on page {page_num}: {e}")
            page_num += 1
            continue

    logger.info(f"CNCEF: Total scraped = {len(members)} members")
    return members
