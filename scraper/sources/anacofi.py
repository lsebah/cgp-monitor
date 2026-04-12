"""
ANACOFI Scraper - Association Nationale des Conseils Financiers
Parses the CIF member export page.
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

EXPORT_URL = "https://adherent.anacofi.asso.fr/action/export-societes-cif"


def scrape_anacofi():
    """
    Scrape the ANACOFI CIF member list from the export page.

    Returns:
        List of normalized member dicts
    """
    logger.info("Starting ANACOFI scrape...")
    members = []

    try:
        resp = fetch(EXPORT_URL, delay=1.0)
        if not resp:
            logger.error("ANACOFI: No response from export URL")
            return members

        content_type = resp.headers.get("Content-Type", "")

        # Could be HTML page with a table/list, or a CSV/text export
        if "text/html" in content_type:
            soup = BeautifulSoup(resp.text, "lxml")
            text = soup.get_text("\n", strip=True)
        else:
            text = resp.text

        # Parse entries - common formats:
        # - "COMPANY NAME (SIREN)" or "COMPANY NAME - SIREN"
        # - Table rows with columns
        # - Simple list items

        # Try to find a table first
        if "text/html" in content_type:
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table")
            if table:
                members = _parse_table(table)
                if members:
                    logger.info(f"ANACOFI: Parsed {len(members)} from table")
                    return members

            # Try list items
            items = soup.find_all("li")
            if items and len(items) > 10:
                members = _parse_list_items(items)
                if members:
                    logger.info(f"ANACOFI: Parsed {len(members)} from list")
                    return members

        # Fallback: parse as text with regex
        members = _parse_text(text)
        logger.info(f"ANACOFI: Parsed {len(members)} from text")
        return members

    except Exception as e:
        logger.error(f"ANACOFI: Error: {e}")
        return members


def _parse_table(table):
    """Parse members from an HTML table."""
    members = []
    rows = table.find_all("tr")
    headers = []

    for i, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        cell_texts = [c.get_text(strip=True) for c in cells]

        if i == 0:
            headers = [h.lower() for h in cell_texts]
            continue

        if not cell_texts:
            continue

        data = dict(zip(headers, cell_texts)) if headers else {}

        # Try to extract company name
        name = (data.get("nom", "") or data.get("societe", "") or
                data.get("raison sociale", "") or data.get("denomination", ""))
        if not name and cell_texts:
            name = cell_texts[0]

        if not name or len(name) < 2:
            continue

        # SIREN
        siren = (data.get("siren", "") or data.get("n siren", "") or
                 data.get("numero siren", ""))
        if not siren:
            for text in cell_texts:
                match = re.match(r'^\d{9,14}$', text.replace(" ", ""))
                if match:
                    siren = text
                    break

        # City
        city = data.get("ville", "") or data.get("city", "")

        # Postal code
        postal_code = data.get("code postal", "") or data.get("cp", "")

        member = make_member_dict(
            company_name=name,
            siren=siren,
            postal_code=postal_code,
            city=city,
            activities=["CIF"],
            source="anacofi",
        )
        members.append(member)

    return members


def _parse_list_items(items):
    """Parse members from HTML list items."""
    members = []
    for item in items:
        text = item.get_text(strip=True)
        if not text or len(text) < 3:
            continue

        # Pattern: "COMPANY NAME ( SIREN )" or "COMPANY NAME (SIREN)"
        match = re.match(r'^(.+?)\s*\(\s*(\d{9,14})\s*\)\s*$', text)
        if match:
            name = match.group(1).strip()
            siren = match.group(2)
        else:
            # Pattern: "COMPANY NAME - SIREN"
            match = re.match(r'^(.+?)\s*[-–]\s*(\d{9,14})\s*$', text)
            if match:
                name = match.group(1).strip()
                siren = match.group(2)
            else:
                name = text.strip()
                siren = ""

        if name and len(name) > 2:
            member = make_member_dict(
                company_name=name,
                siren=siren,
                activities=["CIF"],
                source="anacofi",
            )
            members.append(member)

    return members


def _parse_text(text):
    """Parse members from raw text."""
    members = []
    seen = set()

    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) < 3:
            continue

        # Skip headers and navigation
        if any(kw in line.lower() for kw in ["anacofi", "connexion", "accueil", "recherche", "menu"]):
            continue

        # Pattern: "COMPANY NAME ( SIREN )" or "COMPANY NAME (SIREN)"
        match = re.match(r'^[\*\-\s]*(.+?)\s*\(\s*(\d{9,14})\s*\)\s*$', line)
        if match:
            name = match.group(1).strip()
            siren = match.group(2)
        else:
            # Pattern: "COMPANY NAME - SIREN"
            match = re.match(r'^[\*\-\s]*(.+?)\s*[-–]\s*(\d{9,14})\s*$', line)
            if match:
                name = match.group(1).strip()
                siren = match.group(2)
            else:
                # Just a company name (line that looks like a name, not a header)
                if re.match(r'^[A-Z]', line) and len(line) > 3 and len(line) < 100:
                    name = line
                    siren = ""
                else:
                    continue

        # Dedup
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        member = make_member_dict(
            company_name=name,
            siren=siren,
            activities=["CIF"],
            source="anacofi",
        )
        members.append(member)

    return members
