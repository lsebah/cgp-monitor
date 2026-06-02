"""
ORIAS Comprehensive CGP Scraper - Scrape all registered CGPs from ORIAS.
This supplements the official directories (CNCGP, CNCEF, ANACOFI)
by finding all CIF intermediaries registered with ORIAS.
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.orias.fr/search"


def scrape_orias_cgps():
    """
    Scrape ORIAS registry for all CIF (Conseil en Investissements Financiers)
    intermediaries. This is a comprehensive approach to find CGPs that may not
    be listed in official CNCGP, CNCEF, or ANACOFI directories.

    Returns:
        List of normalized member dicts
    """
    logger.info("Starting comprehensive ORIAS CGP scrape...")
    members = []
    seen = set()

    # Multiple search strategies to capture all CGPs
    search_queries = [
        "Conseil Investissements Financiers",  # Official term
        "CIF",                                   # Abbreviation
        "Gestion patrimoine",                    # Common service
        "Conseiller patrimoine",                 # Job title
        "Gestionnaire actifs",                   # Asset management
        "Conseils financiers",                   # General term
    ]

    total_attempts = 0
    total_found = 0

    for query in search_queries:
        try:
            logger.info(f"ORIAS search query: '{query}'...")
            total_attempts += 1

            resp = fetch(
                SEARCH_URL,
                method="GET",
                params={"q": query},
                delay=1.0,
            )

            if not resp:
                logger.warning(f"No response for query '{query}'")
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # Try multiple selectors for result items (ORIAS structure may vary)
            result_items = []
            for selector in ["div.result", "div.item", "div.listing", "li.result", "article"]:
                items = soup.select(selector)
                if items:
                    result_items = items
                    logger.debug(f"Found {len(items)} results with selector '{selector}'")
                    break

            if not result_items:
                # Fallback: look for any divs that contain ORIAS numbers
                all_divs = soup.find_all("div")
                result_items = [d for d in all_divs if re.search(r'\d{8}', d.get_text())]
                logger.debug(f"Fallback: Found {len(result_items)} divs with ORIAS patterns")

            logger.info(f"Processing {len(result_items)} results for '{query}'...")

            for item in result_items[:200]:  # Limit per query to avoid excessive scraping
                try:
                    text = item.get_text(" ", strip=True)

                    # Extract company name (usually first meaningful text)
                    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 2]
                    if not lines:
                        continue

                    company_name = lines[0]

                    # Validation: reasonable length
                    if len(company_name) < 3 or len(company_name) > 250:
                        continue

                    # Avoid duplicates
                    key = company_name.lower()
                    if key in seen:
                        continue
                    seen.add(key)

                    # Extract ORIAS number (8 digits)
                    orias_match = re.search(r'\b(\d{8})\b', text)
                    orias_number = orias_match.group(1) if orias_match else ""

                    # Skip if no ORIAS number (not officially registered)
                    if not orias_number:
                        continue

                    # Check registration status - skip inactive
                    if re.search(r'\b(radi[ée]|supprim[ée]|inactif|démissionnaire)\b', text, re.I):
                        logger.debug(f"Skipping inactive: {company_name}")
                        continue

                    # Detect activities
                    activities = []
                    if re.search(r'\b(Conseil|CIF|Investissements?\s+Financiers?)\b', text, re.I):
                        activities.append("CIF")
                    if re.search(r'\b(Courtier|COA)\b', text, re.I):
                        activities.append("COA")
                    if re.search(r'\b(Agent\s+(Général|Lie)|AGA|ALPSI)\b', text, re.I):
                        activities.append("AGA")
                    if re.search(r'\b(Mandataire|MIA)\b', text, re.I):
                        activities.append("MIA")

                    # Require at least CIF or related activity
                    if not activities:
                        continue

                    member = make_member_dict(
                        company_name=company_name,
                        orias_number=orias_number,
                        activities=activities,
                        source="orias",
                        source_url=f"{SEARCH_URL}?q={query}",
                    )
                    members.append(member)
                    total_found += 1

                except Exception as e:
                    logger.debug(f"Error parsing result item: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Error scraping query '{query}': {e}")

    logger.info(f"ORIAS scrape complete: {total_found} CGP members found from {total_attempts} search queries")
    return members
