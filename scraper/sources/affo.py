"""
AFFO Scraper - Association Francaise du Family Office
The AFFO does not have a public member directory.
This module scrapes:
1. The governance page for board members and their organizations
2. Known member references from public pages

Since AFFO has ~120 members behind a login wall, this scraper
provides a partial list from publicly available information.
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, make_member_dict

logger = logging.getLogger(__name__)

ORG_URL = "https://www.affo.fr/fr/nous-connaitre/l-organisation"


def scrape_affo():
    """
    Scrape known AFFO members from the governance/organization page.

    Returns:
        List of normalized member dicts
    """
    logger.info("Starting AFFO scrape...")
    members = []

    try:
        resp = fetch(ORG_URL, delay=1.0)
        if not resp:
            logger.error("AFFO: No response from organization page")
            return members

        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text("\n", strip=True)

        # Parse board members - look for name + organization patterns
        # The page typically has names in bold/strong with organizations nearby
        seen = set()

        # Strategy 1: Find strong/b elements that look like names
        for el in soup.find_all(["strong", "b", "h3", "h4"]):
            name_text = el.get_text(strip=True)
            if not name_text or len(name_text) < 3 or len(name_text) > 60:
                continue
            # Skip if it looks like a title/heading
            if any(kw in name_text.lower() for kw in [
                "conseil", "bureau", "equipe", "administration",
                "président", "trésor", "secrétaire", "affo",
                "délégué", "commissaire", "organisation"
            ]):
                continue

            # Check if this looks like a person name (2+ words, capitalized)
            words = name_text.split()
            if len(words) < 2:
                continue
            if not all(w[0].isupper() or w[0] == 'd' for w in words if w not in ['de', 'du', 'la', 'le', 'des']):
                continue

            # Look for organization in nearby text
            parent = el.parent
            if parent:
                sibling_text = parent.get_text(" ", strip=True)
                # Try to extract org name after the person name
                org = ""
                after_name = sibling_text.split(name_text)[-1].strip() if name_text in sibling_text else ""
                # Common patterns: "Name – Org" or "Name, Org" or separate lines
                org_match = re.match(r'^[\s–\-,/]+(.+?)(?:\s*$|\s*\n)', after_name)
                if org_match:
                    org = org_match.group(1).strip()

                key = name_text.lower()
                if key in seen:
                    continue
                seen.add(key)

                # Create member entry for the organization
                if org and len(org) > 2:
                    member = make_member_dict(
                        company_name=org,
                        directors=[{"name": name_text, "role": "Membre AFFO"}],
                        activities=["CIF"],
                        source="affo",
                        source_url=ORG_URL,
                    )
                    members.append(member)
                else:
                    # Just record the person as an individual member
                    member = make_member_dict(
                        company_name=name_text,
                        directors=[{"name": name_text, "role": "Membre AFFO"}],
                        activities=["CIF"],
                        source="affo",
                        source_url=ORG_URL,
                    )
                    members.append(member)

        # Strategy 2: Add known AFFO member organizations from public references
        # These are organizations publicly identified as AFFO members
        known_members = [
            ("Groupe Henner Family Office", "Charles-Henri Bujard", "President AFFO"),
            ("JCDecaux Holding", "Gwénaëlle Peyraud", "Tresorier AFFO"),
            ("Banque Transatlantique", "Valérie Mouchabac-Hutman", "Secretaire General AFFO"),
            ("Etablissements Peugeot Freres", "Bertrand Michaud", "Administrateur AFFO"),
            ("Colam Entreprendre", "Matthieu Coisne", "Administrateur AFFO"),
            ("Groupe Mansartis", "Patricia de La Forest Divonne", "Administrateur AFFO"),
            ("HEREST Multi Family Office", "Jérôme Jambert", "Administrateur AFFO"),
            ("OFILAE", "Jean-Marc Aveline", "Administrateur AFFO"),
            ("Yards", "Jérôme Barré", "Administrateur AFFO"),
        ]

        for org_name, director_name, role in known_members:
            key = org_name.lower()
            if key in seen:
                continue
            seen.add(key)

            member = make_member_dict(
                company_name=org_name,
                directors=[{"name": director_name, "role": role}],
                activities=["CIF"],
                source="affo",
                source_url=ORG_URL,
            )
            members.append(member)

    except Exception as e:
        logger.error(f"AFFO: Error: {e}")

    logger.info(f"AFFO: Total = {len(members)} members (partial - no public annuaire)")
    return members
