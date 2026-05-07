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
# Laplace (= Groupe Crystal + Witam + Financière du Capitole)
# ============================================================
LAPLACE_URL = "https://laplace-groupe.com/implantations-france-metropolitaine"


def scrape_laplace():
    """61 .place-info cards: name + address + phone in one block."""
    logger.info("UCGP/Laplace: fetching list...")
    try:
        resp = fetch(LAPLACE_URL, delay=1.0)
        if not resp:
            return []
    except Exception as e:
        logger.error(f"UCGP/Laplace: fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("div.place-info")
    members = []
    seen = set()
    for card in cards:
        name_el = card.select_one(".fw-700") or card.find("div")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        # Address + phone are inside spans of the <p>
        spans = card.select("p span")
        street = postal = city = phone = ""
        if spans:
            street = spans[0].get_text(" ", strip=True) if len(spans) > 0 else ""
            # Second span: "13290 Aix-en-Provence" — split CP + city
            if len(spans) > 1:
                addr2 = spans[1].get_text(" ", strip=True)
                m = re.match(r"(\d{5})\s+(.+)", addr2)
                if m:
                    postal, city = m.group(1), m.group(2).strip()
                else:
                    city = addr2
            if len(spans) > 2:
                phone = spans[2].get_text(" ", strip=True)

        member = make_member_dict(
            company_name=name,
            address_street=street,
            postal_code=postal,
            city=city,
            phone=phone,
            activities=["CIF"],
            source="ucgp",
            source_url=LAPLACE_URL,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True, "groupement": "Laplace (Groupe Crystal)",
        }
        member["groupement"] = "Laplace (Groupe Crystal)"
        members.append(member)
    logger.info(f"UCGP/Laplace: {len(members)} cabinets")
    return members


# ============================================================
# Cercle France Patrimoine
# ============================================================
CERCLE_FRANCE_URL = "https://cercle-france-patrimoine.fr/membres/"


def scrape_cercle_france_patrimoine():
    """The members list is rendered as a flat text block, not in cards.
    Pattern per entry: '<Cabinet> <Director> <Street>, <Postal> <City>'.
    A single cabinet may appear several times (one entry per director)."""
    logger.info("UCGP/CFP: fetching list...")
    try:
        resp = fetch(CERCLE_FRANCE_URL, delay=1.0)
        if not resp:
            return []
    except Exception as e:
        logger.error(f"UCGP/CFP: fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    # Match every street+postal+city in the page; the chunk between two matches
    # is one (or more) entries.
    addr_re = re.compile(
        r"(\d+[^,;]{2,80}?)[,\s]\s*(\d{5})\s+([A-ZÀ-Ÿ][A-Za-zÀ-ÿ\-' ]+?)(?=\s+[A-ZÀ-Ÿ]|\s+Nos derniers|\s*$)",
        re.UNICODE,
    )

    members = []
    seen = set()  # dedup by (cabinet name, postal)
    last_end = 0
    for m in addr_re.finditer(text):
        chunk = text[last_end:m.start()].strip(" ,;-")
        # The chunk is "<Cabinet> <FirstName> <LastName>". The cabinet name
        # is usually 2-5 words and often contains a "generic" noun that
        # signals it isn't a person name (Patrimoine, Conseil, Gestion,
        # Cabinet, Financière, ...). The director name is the LAST 2 (rarely
        # 3) capitalized tokens. Heuristic: split tokens, walk from the end,
        # accept up to 3 consecutive proper-noun tokens as the director.
        words = chunk.split()
        # Drop trailing weird stuff
        while words and words[-1] in ("-", "–", ","):
            words.pop()
        director_name = ""
        cabinet_name = chunk
        # Words that look like first-letter-capital but are commonly part of
        # CGP cabinet names (so they should NOT be classified as person tokens).
        CABINET_WORDS = {
            "conseil", "conseils", "patrimoine", "cabinet", "gestion",
            "cie", "financière", "financiere", "capital", "confiance",
            "espace", "privée", "privee", "associés", "associes",
            "consulting", "investissement", "investissements", "expert",
        }
        person_tokens = []
        # Accept compound names like "Vilela-Martins" (uppercase after hyphen).
        NAME_TOKEN_RE = re.compile(r"^[A-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ'\-]+$")
        for w in reversed(words):
            if not NAME_TOKEN_RE.match(w):
                break
            if w.lower() in CABINET_WORDS:
                # Stop here — this token belongs to the cabinet name
                break
            person_tokens.insert(0, w)
            if len(person_tokens) >= 3:
                break
        if 2 <= len(person_tokens) <= 3:
            director_name = " ".join(person_tokens)
            cabinet_name = " ".join(words[: -len(person_tokens)])

        cabinet_name = re.sub(r"\s+", " ", cabinet_name).strip(" ,-")
        # Skip junk
        if (not cabinet_name) or len(cabinet_name) < 3 or len(cabinet_name) > 70:
            last_end = m.end()
            continue
        if any(skip in cabinet_name.lower() for skip in
               ("dix-sept cabinets", "président", "directeur", "comité de direction",
                "objectifs", "valeurs")):
            last_end = m.end()
            continue

        street = m.group(1).strip()
        postal = m.group(2)
        city = m.group(3).strip()

        key = (cabinet_name.lower(), postal)
        directors = []
        if director_name:
            directors.append({"name": director_name, "role": "Adherent"})

        if key in seen:
            # Same cabinet seen at this address — append director if new
            existing = next((mb for mb in members
                             if mb["company_name"].lower() == cabinet_name.lower()
                             and mb["address"].get("postal_code") == postal), None)
            if existing and director_name:
                names = {d["name"].lower() for d in existing.get("directors", [])}
                if director_name.lower() not in names:
                    existing.setdefault("directors", []).append(
                        {"name": director_name, "role": "Adherent"}
                    )
            last_end = m.end()
            continue
        seen.add(key)

        member = make_member_dict(
            company_name=cabinet_name,
            address_street=street,
            postal_code=postal,
            city=city,
            activities=["CIF"],
            directors=directors,
            source="ucgp",
            source_url=CERCLE_FRANCE_URL,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True, "groupement": "Cercle France Patrimoine",
        }
        member["groupement"] = "Cercle France Patrimoine"
        members.append(member)
        last_end = m.end()

    logger.info(f"UCGP/CFP: {len(members)} cabinets")
    return members


# ============================================================
# Cyrus Conseil (Cyrus / Herez)
# ============================================================
CYRUS_URL = "https://www.cyrusconseil.fr/implantations"


def scrape_cyrus():
    """Page lists ~6 'Cyrus Herez <City>' bureaus as <h3>, each followed by
    address + phone in sibling elements."""
    logger.info("UCGP/Cyrus: fetching list...")
    try:
        resp = fetch(CYRUS_URL, delay=1.0, verify=False)
        if not resp:
            return []
    except Exception as e:
        logger.error(f"UCGP/Cyrus: fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    members = []
    seen = set()
    for h in soup.find_all("h3"):
        name = h.get_text(strip=True)
        if not name.lower().startswith("cyrus"):
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())

        # Walk siblings + parent to find address + phone in nearby text
        block = h.find_parent("div") or h.parent
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True)) if block else ""
        # Phone (FR format)
        phone_match = re.search(r"\b0[1-9](?:[\s.]?\d{2}){4}\b", text)
        phone = phone_match.group(0) if phone_match else ""
        # Address: postal + city
        m = re.search(r"(\d{5})\s+([A-ZÀ-Ÿ][A-Za-zÀ-ÿ\-' ]+?)(?:\s+\d|\s+T|\s*$)", text)
        postal = city = street = ""
        if m:
            postal = m.group(1)
            city = m.group(2).strip()
            # Street = chunk before the postal code
            before = text[: m.start()].rstrip(", ")
            # Trim everything before the cabinet name
            idx = before.lower().rfind(name.lower())
            if idx >= 0:
                before = before[idx + len(name):].strip(" ,-")
            street = before[-200:].strip()

        member = make_member_dict(
            company_name=name,
            address_street=street,
            postal_code=postal,
            city=city,
            phone=phone,
            activities=["CIF"],
            source="ucgp",
            source_url=CYRUS_URL,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True, "groupement": "Cyrus Conseil",
        }
        member["groupement"] = "Cyrus Conseil"
        members.append(member)
    logger.info(f"UCGP/Cyrus: {len(members)} cabinets")
    return members


# ============================================================
# Magnacarta — Drupal site, 7 region columns each containing cabinet links
# ============================================================
MAGNACARTA_URL = "https://www.magnacarta.fr/carte-des-cabinets"


def scrape_magnacarta():
    """Region columns list links to cabinet detail pages. Each cabinet has its
    own page on magnacarta.fr or sometimes an external website."""
    logger.info("UCGP/Magnacarta: fetching list...")
    try:
        resp = fetch(MAGNACARTA_URL, delay=1.0)
        if not resp:
            return []
    except Exception as e:
        logger.error(f"UCGP/Magnacarta: fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    # Cabinet links are inside the 'paragraphe-column' columns; each column is
    # a region label + list of <a> to cabinet pages.
    cabinet_links = []
    for col in soup.select("div.paragraphe-column"):
        for a in col.find_all("a", href=True):
            href = a.get("href", "")
            txt = a.get_text(" ", strip=True)
            if not txt or len(txt) < 2 or len(txt) > 80:
                continue
            # Only keep links to a cabinet page (internal or external)
            if href.startswith("http") or href.startswith("/"):
                cabinet_links.append((txt, urljoin(MAGNACARTA_URL, href)))

    members = []
    seen = set()
    for name, url in cabinet_links:
        key = name.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        # Light enrichment from the cabinet detail page
        extra = _scrape_magnacarta_detail(url)
        member = make_member_dict(
            company_name=name,
            address_street=extra.get("street", ""),
            postal_code=extra.get("postal_code", ""),
            city=extra.get("city", ""),
            phone=extra.get("phone", ""),
            email=extra.get("email", ""),
            website=extra.get("website", url if not url.startswith(MAGNACARTA_URL) else ""),
            activities=["CIF"],
            directors=extra.get("directors", []),
            source="ucgp",
            source_url=url,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True, "groupement": "Magnacarta",
        }
        member["groupement"] = "Magnacarta"
        members.append(member)
    logger.info(f"UCGP/Magnacarta: {len(members)} cabinets")
    return members


def _scrape_magnacarta_detail(url):
    """Magnacarta cabinet detail pages embed Magnacarta HQ legal mentions
    (72030 Le Mans CEDEX) and Magnacarta corporate address (32 rue de la
    République 69002 Lyon) — both must be filtered OUT to avoid stamping
    every cabinet with the wrong address. We mostly trust the cabinet email
    (mailto), derive a likely website from its domain, and skip address
    extraction entirely (data.gouv will provide it later via SIREN-by-name)."""
    info = {}
    try:
        resp = fetch(url, delay=0.7, max_retries=2)
        if not resp:
            return info
        soup = BeautifulSoup(resp.text, "lxml")

        # Mailto blocklist: organisations cited as legal mediators on every
        # cabinet page (CMAP, AMF, ORIAS, ANACOFI...) — they're not the
        # cabinet's email.
        EMAIL_BLOCKLIST = (
            "magnacarta", "cmap.fr", "amf-france", "orias",
            "anacofi", "cncgp", "cncef", "consommation@",
            "mediation", "info-conso",
        )

        # Email: pick the first mailto whose domain isn't a known mediator
        for el in soup.find_all("a", href=re.compile(r"^mailto:")):
            raw = el.get("href", "").replace("mailto:", "").strip()
            cleaned = clean_email(raw)
            if cleaned and not any(b in cleaned for b in EMAIL_BLOCKLIST):
                info["email"] = cleaned
                # Derive a plausible website from the email domain
                domain = cleaned.split("@", 1)[1] if "@" in cleaned else ""
                if domain and "." in domain:
                    info["website"] = f"https://{domain}/"
                break
        # External website link if not derived from email
        if "website" not in info:
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if href.startswith("http") and "magnacarta.fr" not in href \
                        and not any(d in href for d in
                                    ("facebook", "linkedin", "twitter",
                                     "instagram", "youtube", "amf-france", "orias")):
                    info["website"] = href
                    break
        # Address extraction is intentionally skipped — see docstring.
    except Exception as e:
        logger.debug(f"  Magnacarta detail failed for {url}: {e}")
    return info


# ============================================================
# Cercle Valeurs Patrimoine — JS-rendered, requires Playwright
# ============================================================
CERCLE_VALEURS_URL = "https://lecerclevaleurspatrimoine.fr/les-cabinets/"


def scrape_cercle_valeurs():
    """List page is JS-rendered; cabinet cards appear after a short delay."""
    logger.info("UCGP/CVP: fetching with Playwright...")
    try:
        html = fetch_browser(CERCLE_VALEURS_URL, wait_ms=4000)
    except Exception as e:
        logger.error(f"UCGP/CVP: fetch failed: {e}")
        return []

    soup = BeautifulSoup(html, "lxml")
    members = []
    seen = set()
    # WordPress site — cabinet cards likely come as articles or .cabinet-* divs.
    candidates = (soup.select("article")
                  or soup.select("div[class*='cabinet']")
                  or soup.select("div[class*='card']"))
    for card in candidates:
        h = card.find(["h2", "h3", "h4"])
        if not h:
            continue
        name = h.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        if any(skip in name.lower() for skip in ("cookies", "menu", "nous contacter")):
            continue
        seen.add(name.lower())
        # Address + city heuristic
        text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
        addr = _normalize_address_block(text)
        # Website link if any
        website = ""
        for a in card.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("http") and "lecerclevaleurspatrimoine.fr" not in href:
                website = href
                break

        member = make_member_dict(
            company_name=name,
            address_street=addr.get("street", ""),
            postal_code=addr.get("postal_code", ""),
            city=addr.get("city", ""),
            website=website,
            activities=["CIF"],
            source="ucgp",
            source_url=CERCLE_VALEURS_URL,
        )
        member.setdefault("associations", {})["ucgp"] = {
            "member": True, "groupement": "Cercle Valeurs Patrimoine",
        }
        member["groupement"] = "Cercle Valeurs Patrimoine"
        members.append(member)
    logger.info(f"UCGP/CVP: {len(members)} cabinets")
    return members


# ============================================================
# Top-level orchestrator
# ============================================================
def scrape_all_ucgp():
    """Run every implemented UCGP scraper and return one merged list."""
    all_members = []
    for fn, label in [
        (scrape_entrepreneurs, "Entrepreneurs"),
        (scrape_finindep, "Finindep"),
        (scrape_actualis, "Actualis"),
        (scrape_laplace, "Laplace"),
        (scrape_cercle_france_patrimoine, "CFP"),
        (scrape_cyrus, "Cyrus"),
        (scrape_magnacarta, "Magnacarta"),
        (scrape_cercle_valeurs, "CVP"),
    ]:
        try:
            ms = fn()
            all_members.extend(ms)
        except Exception as e:
            logger.error(f"UCGP/{label} crashed: {e}")
    return all_members
