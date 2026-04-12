"""
ORIAS Enrichment - Organisme pour le Registre des Intermediaires
Looks up members by name or SIREN on the public ORIAS registry.
Used for enrichment only, not as a primary scraping source.
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.orias.fr/search"


def enrich_member(member, by_field="company_name"):
    """
    Look up a member on ORIAS and enrich with registration data.

    Args:
        member: Member dict to enrich
        by_field: Field to search by ("company_name" or "orias_number")

    Returns:
        Updated member dict with ORIAS data
    """
    if by_field == "orias_number" and member.get("orias_number"):
        query = member["orias_number"]
    elif by_field == "company_name":
        query = member.get("company_name", "")
    else:
        return member

    if not query:
        return member

    try:
        # ORIAS search is form-based
        resp = fetch(
            SEARCH_URL,
            method="GET",
            params={"q": query},
            delay=2.0,  # Be gentle with ORIAS
        )
        if not resp:
            return member

        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)

        # Extract ORIAS number
        orias_match = re.search(r'(\d{8})', text)
        if orias_match and not member.get("orias_number"):
            member["orias_number"] = orias_match.group(1)

        # Extract registration categories
        categories = []
        for cat, pattern in [
            ("CIF", r'Conseil(?:ler)?\s+en\s+Investissements?\s+Financiers?'),
            ("COA", r'Courtier\s+(?:en\s+)?[Aa]ssurance'),
            ("AGA", r'Agent\s+[Gg]eneral\s+d.Assurance'),
            ("MIA", r'Mandataire\s+d.Intermediaire'),
            ("IOBSP", r'Intermediaire\s+en\s+Operations\s+de\s+Banque'),
            ("ALPSI", r'Agent\s+Lie'),
            ("IFP", r'Intermediaire\s+en\s+Financement\s+Participatif'),
        ]:
            if re.search(pattern, text, re.I):
                categories.append(cat)

        if categories:
            member["orias_categories"] = categories
            # Also update activities
            current = set(member.get("activities", []))
            current.update(categories)
            member["activities"] = list(current)

        # Check status
        if re.search(r'inscri(?:t|ption)\s+activ', text, re.I):
            member["orias_status"] = "active"
        elif re.search(r'radi[ée]|supprim[ée]|inactif', text, re.I):
            member["orias_status"] = "inactive"

        return member

    except Exception as e:
        logger.debug(f"ORIAS lookup failed for {query}: {e}")
        return member


def batch_enrich(members, max_lookups=100):
    """
    Enrich a batch of members with ORIAS data.

    Args:
        members: List of member dicts
        max_lookups: Maximum number of ORIAS lookups (rate limiting)

    Returns:
        List of enriched member dicts
    """
    logger.info(f"ORIAS enrichment: {min(max_lookups, len(members))} lookups planned")
    count = 0

    for member in members:
        if count >= max_lookups:
            break

        # Skip if already has ORIAS data
        if member.get("orias_number") and member.get("orias_status"):
            continue

        # Prioritize members without ORIAS number
        if member.get("orias_number"):
            member = enrich_member(member, by_field="orias_number")
        else:
            member = enrich_member(member, by_field="company_name")

        count += 1

    logger.info(f"ORIAS enrichment: completed {count} lookups")
    return members
