"""
CNCGP Scraper - Chambre Nationale des Conseillers en Gestion de Patrimoine
Scrapes the directory at https://www.cncgp.fr/annuaire by iterating departments.
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

ANNUAIRE_URL = "https://www.cncgp.fr/annuaire"


def _parse_results_page(soup):
    """Parse member entries from a CNCGP results page."""
    members_data = []

    # Try various container patterns
    results = soup.find_all("div", class_=re.compile(r"result|member|adherent|card|item", re.I))
    if not results:
        results = soup.find_all("article")
    if not results:
        # Try table-based layout
        rows = soup.find_all("tr")
        if rows:
            results = rows[1:]  # skip header row

    for item in results:
        data = {}
        text = item.get_text(" ", strip=True)
        if not text or len(text) < 5:
            continue

        # Company name - usually the first strong/heading element
        name_el = item.find(["h2", "h3", "h4", "strong", "b"])
        if name_el:
            data["company_name"] = name_el.get_text(strip=True)
        else:
            # First link might be the name
            link = item.find("a")
            if link:
                data["company_name"] = link.get_text(strip=True)

        if not data.get("company_name"):
            continue

        # Detail link
        link = item.find("a", href=True)
        if link:
            href = link.get("href", "")
            if href.startswith("/") or "cncgp.fr" in href:
                if href.startswith("/"):
                    href = f"https://www.cncgp.fr{href}"
                data["detail_url"] = href

        # Address
        addr_text = text
        # Try to find postal code + city pattern
        pc_match = re.search(r'(\d{5})\s+([A-Z\s\-]+)', addr_text)
        if pc_match:
            data["postal_code"] = pc_match.group(1)
            data["city"] = pc_match.group(2).strip().title()

        # Street address
        street_match = re.search(r'(\d+[\s,]+(?:rue|avenue|boulevard|place|chemin|impasse|allee|cours)\s+[^,\d]+)', text, re.I)
        if street_match:
            data["address_street"] = street_match.group(1).strip()

        # Phone
        phone_match = re.search(r'(?:Tel|Telephone|Tel\.?)\s*:?\s*([0-9\s\.\-+]{10,})', text, re.I)
        if phone_match:
            data["phone"] = phone_match.group(1).strip()
        else:
            # Direct phone pattern
            phone_el = item.find("a", href=re.compile(r"^tel:"))
            if phone_el:
                data["phone"] = phone_el.get("href", "").replace("tel:", "")

        # Email
        email_el = item.find("a", href=re.compile(r"^mailto:"))
        if email_el:
            data["email"] = email_el.get("href", "").replace("mailto:", "")
        else:
            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
            if email_match:
                data["email"] = email_match.group(0)

        # Activities
        activities = []
        for kw, act in [("cif", "CIF"), ("assurance", "COA"), ("iobsp", "IOBSP"),
                        ("immobilier", "Immobilier"), ("courtier", "COA")]:
            if kw in text.lower():
                activities.append(act)
        data["activities"] = activities

        members_data.append(data)

    return members_data


def _parse_detail_page(url):
    """Fetch and parse a CNCGP member detail page."""
    try:
        resp = fetch(url, delay=1.0)
        if not resp:
            return {}
        soup = BeautifulSoup(resp.text, "lxml")
        info = {}

        text = soup.get_text(" ", strip=True)

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
            if href.startswith("http") and "cncgp.fr" not in href:
                link_text = a.get_text(strip=True).lower()
                if any(w in link_text for w in ["site", "web", "www"]) or any(w in href for w in ["www.", ".fr", ".com"]):
                    info["website"] = href
                    break

        # Dirigeant / Directors
        dirigeant_match = re.search(
            r'(?:Dirigeant|Gerant|President|Representant\s+legal|Contact)\s*:?\s*(.+?)(?:\n|$|<)',
            text, re.I
        )
        if dirigeant_match:
            name = dirigeant_match.group(1).strip()
            # Clean up
            name = re.sub(r'\s+', ' ', name)
            if len(name) > 3 and len(name) < 80:
                info["director_name"] = name

        # ORIAS number
        orias_match = re.search(r'(?:ORIAS|orias)\s*:?\s*(\d{8})', text)
        if orias_match:
            info["orias_number"] = orias_match.group(1)

        # Address
        addr_match = re.search(r'(\d+[\s,]+(?:rue|avenue|boulevard|place|chemin)\s+[^,\n]+)', text, re.I)
        if addr_match:
            info["address_street"] = addr_match.group(1).strip()

        pc_match = re.search(r'(\d{5})\s+([A-Z][a-zA-Z\s\-]+)', text)
        if pc_match:
            info["postal_code"] = pc_match.group(1)
            info["city"] = pc_match.group(2).strip()

        return info
    except Exception as e:
        logger.debug(f"Error parsing CNCGP detail {url}: {e}")
        return {}


def scrape_cncgp(departments=None, enrich_details=True):
    """
    Scrape the CNCGP directory by department.

    Args:
        departments: List of department codes to scrape (default: all)
        enrich_details: If True, follow detail links for full info

    Returns:
        List of normalized member dicts
    """
    from config import DEPARTMENTS

    logger.info("Starting CNCGP scrape...")
    dept_list = departments or list(DEPARTMENTS.keys())
    members = []
    seen_names = set()

    for dept_code in dept_list:
        dept_name = DEPARTMENTS.get(dept_code, dept_code)
        logger.info(f"CNCGP: Scraping department {dept_code} ({dept_name})...")

        try:
            # Try search by department - the CNCGP form likely uses POST or GET params
            # Attempt with GET parameters first
            search_url = f"{ANNUAIRE_URL}?departement={dept_code}"
            resp = fetch(search_url, delay=1.0)
            if not resp:
                # Try POST form
                resp = fetch(
                    ANNUAIRE_URL,
                    method="POST",
                    data={"departement": dept_code, "activite": "", "search": ""},
                    delay=1.0,
                )
            if not resp:
                logger.warning(f"CNCGP: No response for dept {dept_code}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            entries = _parse_results_page(soup)

            # Handle pagination within department
            page = 2
            while page <= 20:  # Safety limit per department
                next_link = soup.find("a", class_=re.compile(r"next", re.I))
                if not next_link:
                    paging = soup.find(class_=re.compile(r"pagination", re.I))
                    if paging:
                        current = paging.find(class_=re.compile(r"current|active", re.I))
                        if current:
                            next_link = current.find_next_sibling("a")
                    if not next_link:
                        break

                next_url = next_link.get("href", "")
                if not next_url:
                    break
                if next_url.startswith("/"):
                    next_url = f"https://www.cncgp.fr{next_url}"

                resp = fetch(next_url, delay=1.0)
                if not resp:
                    break
                soup = BeautifulSoup(resp.text, "lxml")
                page_entries = _parse_results_page(soup)
                if not page_entries:
                    break
                entries.extend(page_entries)
                page += 1

            # Process entries
            for data in entries:
                name = data.get("company_name", "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                # Enrich from detail page
                extra = {}
                if enrich_details and data.get("detail_url"):
                    extra = _parse_detail_page(data["detail_url"])

                # Merge data (detail takes precedence)
                phone = extra.get("phone") or data.get("phone", "")
                email = extra.get("email") or data.get("email", "")
                website = extra.get("website", "")
                directors = []
                if extra.get("director_name"):
                    directors = [{"name": extra["director_name"], "role": "Dirigeant"}]
                orias = extra.get("orias_number", "")
                postal_code = extra.get("postal_code") or data.get("postal_code", "")
                city = extra.get("city") or data.get("city", "")
                street = extra.get("address_street") or data.get("address_street", "")

                member = make_member_dict(
                    company_name=name,
                    orias_number=orias,
                    address_street=street,
                    postal_code=postal_code,
                    city=city,
                    phone=phone,
                    email=email,
                    website=website,
                    activities=data.get("activities", []),
                    directors=directors,
                    source="cncgp",
                    source_url=data.get("detail_url", ""),
                )
                members.append(member)

            logger.info(f"CNCGP: Dept {dept_code} -> {len(entries)} entries")

        except Exception as e:
            logger.error(f"CNCGP: Error for dept {dept_code}: {e}")
            continue

    logger.info(f"CNCGP: Total scraped = {len(members)} members")
    return members
