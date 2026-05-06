"""
UCGP scrapers — Union des Conseillers en Gestion de Patrimoine.

UCGP federates ~15 groupements; this module scrapes the public member
directories of those that publish one. Each scraped cabinet is tagged
with associations.ucgp.member = True AND nested under its specific
groupement (so we keep a clean breakdown by parent group).

Currently implemented:
  - Actualis Associes        (Wix, JS-rendered → Playwright)
  - Le Club des Entrepreneurs (HTML simple)
  - Finindep                  (HTML simple, Bootstrap layout)

Each scraper returns a list of normalized member dicts via
make_member_dict, with source = "ucgp" and groupement set to the
parent group name. The orchestrator scripts/scrape_ucgp.py runs all
of them and smart-merges the results into members.json without
wiping enrichment fields on existing cabinets.
"""
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import fetch, fetch_browser, make_member_dict, clean_phone, clean_email

logger = logging.getLogger(__name__)

UCGP_GROUPEMENTS = {
    "actualis": "Actualis Associes",
    "entrepreneurs": "Le Club des Entrepreneurs CGP",
    "finindep": "Finindep",
    # to add: acogepi, cyrus, witam, cercle_france, cercle_valeur, crip,
    #         crystal, boetie, cgp_priv, capitole, mes_placement, magnacarta
}


def _normalize_address_block(text):
    """Best-effort: pull (street, postal, city) out of a free-form text block."""
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"(.+?)(\d{5})\s+([A-Z][a-zA-Zà-ÿ\-' ]+)", text)
    if not m:
        return {"street": "", "postal_code": "", "city": ""}
    street = re.sub(r"[,;\s]+$", "", m.group(1)).strip()
    return {
        "street": street,
        "postal_code": m.group(2),
        "city": m.group(3).strip(),
    }


# ============================================================
# Actualis Associes — Wix site, requires Playwright
# ============================================================
ACTUALIS_URL = "https://www.actualisassocies.fr/trouver-un-conseiller-en-gestion-de-patrimoine"


def scrape_actualis():
    """Wix-rendered list of conseillers. Each card = name + cabinet + specialties."""
    logger.info("UCGP/Actualis: fetching with Playwright...")
    try:
        html = fetch_browser(ACTUALIS_URL, wait_ms=4000)
    except Exception as e:
        logger.error(f"UCGP/Actualis: fetch failed: {e}")
        return []

    soup = BeautifulSoup(html, "lxml")
    items = soup.select("div.wixui-repeater__item")
    members = []
    seen = set()
    for item in items:
        text = item.get_text(" | ", strip=True)
        if not text or len(text) < 8:
            continue
        # First " | " segment is usually "PRENOM NOM", second "Nom Cabinet"
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) < 2:
            continue
        director_name = parts[0]
        cabinet_name = parts[1]
        # Skip duplicates / headers
        if cabinet_name.lower() in seen:
            continue
        seen.add(cabinet_name.lower())
        if cabinet_name.lower() in {"conseiller", "cgp", "rechercher"}:
            continue
        specialties = parts[2:]
        member = make_member_dict(
            company_name=cabinet_name,
            directors=[{"name": director_name, "role": "Adherent"}],
            activities=["CIF"],
            specialties=specialties[:8],
            source="ucgp",
            source_url=ACTUALIS_URL,
        )
        # Tag groupement nesting
        member.setdefault("associations", {})["ucgp"] = {
            "member": True,
            "groupement": "Actualis Associes",
        }
        member["groupement"] = "Actualis Associes"
        members.append(member)
    logger.info(f"UCGP/Actualis: {len(members)} cabinets")
    return members


# ============================================================
# Le Club des Entrepreneurs CGP — simple HTML
# ============================================================
ENTREPRENEURS_URL = "https://www.entrepreneurs-cgp.com/page/27058/membres/"


def scrape_entrepreneurs(enrich_details=True):
    """Each card links to /page/<id>/<slug>/ with detailed contact info."""
    logger.info("UCGP/Entrepreneurs: fetching list...")
    try:
        resp = fetch(ENTREPRENEURS_URL, delay=1.0)
        if not resp:
            return []
    except Exception as e:
        logger.error(f"UCGP/Entrepreneurs: fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("div.actusite_column_5")
    members = []
    seen = set()
    for card in cards:
        a = card.select_one("a[href]")
        name_el = card.select_one("strong")
        if not a or not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        detail_url = urljoin(ENTREPRENEURS_URL, a.get("href", ""))

        # Description text (just below name)
        desc_el = a.select_one("div.container-texte div") or card.select_one("div div")
        specialties = []
        if desc_el:
            desc = desc_el.get_text(" ", strip=True)
            if desc:
                specialties = [desc[:200]]

        extra = {}
        if enrich_details:
            extra = _scrape_entrepreneurs_detail(detail_url)

        member = make_member_dict(
            company_name=name,
            address_street=extra.get("street", ""),
            postal_code=extra.get("postal_code", ""),
            city=extra.get("city", ""),
            phone=extra.get("phone", ""),
            email=extra.get("email", ""),
            website=extra.get("website", ""),
            activities=["CIF"],
            specialties=specialties,
            directors=extra.get("directors", []),
            source="ucgp",
            source_url=detail_url,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True,
            "groupement": "Le Club des Entrepreneurs CGP",
        }
        member["groupement"] = "Le Club des Entrepreneurs CGP"
        members.append(member)
    logger.info(f"UCGP/Entrepreneurs: {len(members)} cabinets")
    return members


def _scrape_entrepreneurs_detail(url):
    """Fetch the cabinet detail page for street/postal/city/phone/email/website/director."""
    info = {}
    try:
        resp = fetch(url, delay=0.7, max_retries=2)
        if not resp:
            return info
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        # Phone / email
        phone_el = soup.find("a", href=re.compile(r"^tel:"))
        if phone_el:
            info["phone"] = clean_phone(phone_el.get("href", "").replace("tel:", ""))
        email_el = soup.find("a", href=re.compile(r"^mailto:"))
        if email_el:
            info["email"] = clean_email(email_el.get("href", "").replace("mailto:", ""))
        # Website (any external http link that isn't entrepreneurs-cgp.com)
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("http") and "entrepreneurs-cgp.com" not in href \
                    and not any(d in href for d in ("facebook", "linkedin", "twitter", "instagram", "youtube")):
                info["website"] = href
                break
        # Address: postal+city heuristic on full text
        addr = _normalize_address_block(text)
        info.update({k: v for k, v in addr.items() if v})
        # Director: look for "M. <Name>" / "Mme <Name>" / "Dirigeant: <Name>"
        m = re.search(r"(?:Dirigeant|G[ée]rant|Pr[ée]sident|Directeur)\s*:?\s*([A-Z][a-zà-ÿ' \-]{4,60}?)(?=\s*(?:Email|Tel|T[ée]l|Adresse|$))",
                      text)
        if m:
            info["directors"] = [{"name": m.group(1).strip(), "role": "Adherent"}]
    except Exception as e:
        logger.debug(f"  detail fetch failed for {url}: {e}")
    return info


# ============================================================
# Finindep — simple HTML, each card links to /page/<id>/<slug>/
# ============================================================
FININDEP_URL = "https://www.finindep.com/page/10552/noscabinets/"


def scrape_finindep(enrich_details=True):
    logger.info("UCGP/Finindep: fetching list...")
    try:
        resp = fetch(FININDEP_URL, delay=1.0)
        if not resp:
            return []
    except Exception as e:
        logger.error(f"UCGP/Finindep: fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("div.col-xl-4.col-lg-4")
    members = []
    seen = set()
    for card in cards:
        # Each card has multiple <a href> + <strong> name
        strong = card.select_one("strong")
        a = card.select_one("a[href*='/page/']")
        if not strong or not a:
            continue
        name = strong.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        detail_url = urljoin(FININDEP_URL, a.get("href", ""))

        desc_el = card.select_one("p span") or card.select_one("p")
        specialties = []
        if desc_el:
            desc = desc_el.get_text(" ", strip=True)
            if desc and len(desc) > 10:
                specialties = [desc[:200]]

        extra = _scrape_finindep_detail(detail_url) if enrich_details else {}

        member = make_member_dict(
            company_name=name,
            address_street=extra.get("street", ""),
            postal_code=extra.get("postal_code", ""),
            city=extra.get("city", ""),
            phone=extra.get("phone", ""),
            email=extra.get("email", ""),
            website=extra.get("website", ""),
            activities=["CIF"],
            specialties=specialties,
            directors=extra.get("directors", []),
            source="ucgp",
            source_url=detail_url,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True,
            "groupement": "Finindep",
        }
        member["groupement"] = "Finindep"
        members.append(member)
    logger.info(f"UCGP/Finindep: {len(members)} cabinets")
    return members


def _scrape_finindep_detail(url):
    """Same shape as the Entrepreneurs helper — Finindep detail pages have the
    same kind of free-form contact block."""
    info = {}
    try:
        resp = fetch(url, delay=0.7, max_retries=2)
        if not resp:
            return info
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        phone_el = soup.find("a", href=re.compile(r"^tel:"))
        if phone_el:
            info["phone"] = clean_phone(phone_el.get("href", "").replace("tel:", ""))
        email_el = soup.find("a", href=re.compile(r"^mailto:"))
        if email_el:
            info["email"] = clean_email(email_el.get("href", "").replace("mailto:", ""))
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("http") and "finindep.com" not in href \
                    and not any(d in href for d in ("facebook", "linkedin", "twitter", "instagram", "youtube")):
                info["website"] = href
                break
        addr = _normalize_address_block(text)
        info.update({k: v for k, v in addr.items() if v})
        m = re.search(r"(?:Dirigeant|G[ée]rant|Pr[ée]sident|Directeur)\s*:?\s*([A-Z][a-zà-ÿ' \-]{4,60}?)(?=\s*(?:Email|Tel|T[ée]l|Adresse|$))",
                      text)
        if m:
            info["directors"] = [{"name": m.group(1).strip(), "role": "Adherent"}]
    except Exception as e:
        logger.debug(f"  detail fetch failed for {url}: {e}")
    return info


# ============================================================
# Top-level orchestrator
# ============================================================
def scrape_all_ucgp():
    """Run every implemented UCGP scraper and return one merged list."""
    all_members = []
    for fn, label in [(scrape_entrepreneurs, "Entrepreneurs"),
                      (scrape_finindep, "Finindep"),
                      (scrape_actualis, "Actualis")]:
        try:
            ms = fn()
            all_members.extend(ms)
        except Exception as e:
            logger.error(f"UCGP/{label} crashed: {e}")
    return all_members
