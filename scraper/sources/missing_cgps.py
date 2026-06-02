"""
Missing CGPs Search - Automatically search ORIAS for known missing CGPs.
This supplements the official directories (CNCGP, CNCEF, ANACOFI)
for firms not listed in those public annuaires.
"""
import logging
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.orias.fr/search"


def scrape_missing_cgps():
    """
    Search for known missing CGPs on ORIAS.
    Automatically discovers firms from the missing_cgps list.

    Returns:
        List of normalized member dicts
    """
    logger.info("Searching ORIAS for missing CGPs...")
    members = []

    # List of known CGPs that should be included but aren't in official directories
    # Add more firm names here as they are discovered
    missing_cgps = [
        "Alpy Gestion",
        "Blackwell Family Office",
    ]

    for firm_name in missing_cgps:
        try:
            logger.info(f"Searching ORIAS for: {firm_name}...")

            resp = fetch(
                SEARCH_URL,
                method="GET",
                params={"q": firm_name},
                delay=1.5,
            )

            if not resp:
                logger.warning(f"No response for {firm_name}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            text = soup.get_text(" ", strip=True)

            # Extract ORIAS number
            orias_match = re.search(r'\b(\d{8})\b', text)
            orias_number = orias_match.group(1) if orias_match else ""

            # Check registration status
            if "radié" in text.lower() or "inactif" in text.lower():
                logger.info(f"{firm_name}: Marked as inactive in ORIAS")
                continue

            # Detect activities
            activities = []
            if re.search(r'(Conseil|CIF|Investissements?\s+Financiers?)', text, re.I):
                activities.append("CIF")
            if re.search(r'(Courtier|COA)', text, re.I):
                activities.append("COA")
            if re.search(r'(Agent.*Assurance|AGA)', text, re.I):
                activities.append("AGA")

            member = make_member_dict(
                company_name=firm_name,
                orias_number=orias_number,
                activities=activities,
                source="orias",
                source_url=f"{SEARCH_URL}?q={firm_name}",
            )
            members.append(member)
            logger.info(f"Found: {firm_name} (ORIAS: {orias_number or 'N/A'})")

        except Exception as e:
            logger.warning(f"Error searching {firm_name}: {e}")

    logger.info(f"Missing CGPs search: {len(members)} firms found")
    return members
