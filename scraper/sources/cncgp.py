"""
CNCGP Scraper - Chambre Nationale des Conseillers en Gestion de Patrimoine
Scrapes the directory at https://www.cncgp.fr/annuaire using Playwright.

The CNCGP site renders results via JavaScript after form submission.
Requires Playwright with Chromium browser.

HTML structure (verified April 2026):
- Results container: div.oct_annuaire_result_items
- Each card: div.oct_annuaire_result_item
  - Name: div.oct_annuaire_result_item_title
  - Address street: div.oct_annuaire_result_item_address_complete
  - Postal+city: div.oct_annuaire_result_item_address (text after the inner div)
  - Phone: div.oct_annuaire_result_item_telephone > a[href^=tel:]
  - Website: div.oct_annuaire_result_item_site > a
  - Director: div.oct_annuaire_result_item_adherent > span
  - Lat/Lng: data-latitude, data-longitude attributes
- Department select: select[name="departLabel"] with values like "75", "92", etc.
"""
import logging
import re
import time

from bs4 import BeautifulSoup

from .base import make_member_dict

logger = logging.getLogger(__name__)

ANNUAIRE_URL = "https://www.cncgp.fr/annuaire"

# Priority departments (most CGPs, scraped first)
PRIORITY_DEPTS = [
    "75", "92", "69", "33", "13", "31", "44", "06", "78", "91",
    "94", "59", "67", "34", "35", "38", "57", "54", "76", "77",
]


def _parse_results(soup):
    """Parse all result items from a CNCGP results page."""
    members_data = []
    items = soup.select(".oct_annuaire_result_item")

    for item in items:
        # Company name
        title_el = item.select_one(".oct_annuaire_result_item_title")
        if not title_el:
            continue
        name = title_el.get_text(strip=True)
        if not name or len(name) < 2:
            continue

        # Address: street is in the inner div, postal+city is text in the parent
        street = ""
        postal_code = ""
        city = ""
        street_el = item.select_one(".oct_annuaire_result_item_address_complete")
        if street_el:
            street = street_el.get_text(strip=True)

        addr_el = item.select_one(".oct_annuaire_result_item_address")
        if addr_el:
            # Get the full text and extract postal code + city
            addr_text = addr_el.get_text(" ", strip=True)
            # Remove the street part
            if street:
                addr_text = addr_text.replace(street, "").strip()
            # Match postal code (5 digits) + city
            pc_match = re.search(r'(\d{5})\s+([\w\s\-\'\u00C0-\u024F]+)', addr_text)
            if pc_match:
                postal_code = pc_match.group(1)
                city = pc_match.group(2).strip().title()

        # Phone
        phone = ""
        phone_el = item.select_one(".oct_annuaire_result_item_telephone a")
        if phone_el:
            phone = phone_el.get_text(strip=True)

        # Website
        website = ""
        site_el = item.select_one(".oct_annuaire_result_item_site a")
        if site_el:
            website = site_el.get("href", "").strip()

        # Director name
        directors = []
        adherent_el = item.select_one(".oct_annuaire_result_item_adherent span")
        if adherent_el:
            dir_name = " ".join(adherent_el.get_text().split())  # collapse whitespace
            if dir_name and len(dir_name) > 2:
                directors.append({"name": dir_name.title(), "role": "Adherent"})

        members_data.append({
            "company_name": name,
            "address_street": street,
            "postal_code": postal_code,
            "city": city,
            "phone": phone,
            "website": website,
            "directors": directors,
        })

    return members_data


def scrape_cncgp(departments=None):
    """
    Scrape the CNCGP directory using Playwright (headless browser).

    Args:
        departments: List of department codes to scrape (default: all from select)

    Returns:
        List of normalized member dicts
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("CNCGP: Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
        return []

    logger.info("Starting CNCGP scrape (Playwright)...")
    members = []
    seen_names = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        try:
            # Navigate to annuaire
            page.goto(ANNUAIRE_URL, timeout=30000)
            page.wait_for_load_state("networkidle")

            # Get available departments from the select
            if departments is None:
                dept_options = page.query_selector_all('select[name="departLabel"] option')
                departments = []
                for opt in dept_options:
                    val = opt.get_attribute("value")
                    if val:
                        departments.append(val)
                logger.info(f"CNCGP: Found {len(departments)} departments in select")

                # Sort with priority departments first
                priority_set = set(PRIORITY_DEPTS)
                departments = sorted(departments, key=lambda d: (d not in priority_set, d))

            total_depts = len(departments)
            for idx, dept_code in enumerate(departments):
                logger.info(f"CNCGP: [{idx+1}/{total_depts}] Department {dept_code}...")

                try:
                    # Navigate fresh for each department to avoid state issues
                    if idx > 0:
                        page.goto(ANNUAIRE_URL, timeout=30000)
                        page.wait_for_load_state("networkidle")

                    # Select department
                    page.select_option('select[name="departLabel"]', dept_code)

                    # Click search button
                    search_btn = page.query_selector('button[type="submit"]:has-text("Rechercher")')
                    if not search_btn:
                        search_btn = page.query_selector('.oct_annuaire_form button[type="submit"]')
                    if search_btn:
                        search_btn.click()
                    else:
                        logger.warning(f"CNCGP: Search button not found for dept {dept_code}")
                        continue

                    # Wait for results to render
                    page.wait_for_timeout(2000)
                    try:
                        page.wait_for_selector(".oct_annuaire_result_item", timeout=10000)
                    except Exception:
                        logger.info(f"CNCGP: No results for dept {dept_code}")
                        continue

                    # Get page content and parse
                    content = page.content()
                    soup = BeautifulSoup(content, "lxml")
                    entries = _parse_results(soup)

                    dept_count = 0
                    for data in entries:
                        name = data["company_name"]
                        name_key = name.lower()
                        if name_key in seen_names:
                            continue
                        seen_names.add(name_key)

                        member = make_member_dict(
                            company_name=name,
                            address_street=data["address_street"],
                            postal_code=data["postal_code"],
                            city=data["city"],
                            phone=data["phone"],
                            website=data["website"],
                            directors=data["directors"],
                            activities=["CIF"],  # CNCGP members are primarily CIF
                            source="cncgp",
                        )
                        members.append(member)
                        dept_count += 1

                    logger.info(f"CNCGP: Dept {dept_code} -> {dept_count} new ({len(entries)} total)")

                    # Small delay between departments
                    time.sleep(1)

                except Exception as e:
                    logger.error(f"CNCGP: Error for dept {dept_code}: {e}")
                    continue

        except Exception as e:
            logger.error(f"CNCGP: Fatal error: {e}")
        finally:
            browser.close()

    logger.info(f"CNCGP: Total = {len(members)} members across {len(departments)} departments")
    return members
