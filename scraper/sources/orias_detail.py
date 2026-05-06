"""
ORIAS detail-page enricher: adresse + téléphone + email by SIREN.

Why: the existing orias.py only does keyword search and only extracts the ORIAS
number / categories / status. The detail page at /home/showIntermediaire/{SIREN}
exposes "Adresse", "Téléphone public", "Email public" as plain HTML — no
Cloudflare on this endpoint, so simple requests + BeautifulSoup is enough.

Coverage observed on 4/4 random ANACOFI samples: 100% address, 100% phone,
100% email. Target: ~85-90% real coverage on the 3239 ANACOFI members with SIREN.
"""
import html as html_module
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, clean_phone, clean_email, extract_department

logger = logging.getLogger(__name__)

DETAIL_URL = "https://www.orias.fr/home/showIntermediaire/{siren}"


def _extract_label_value(soup, label):
    """Find a <span class="label">{label}</span> + next <span> pattern."""
    for span in soup.find_all("span", class_="label"):
        if span.get_text(strip=True).lower() == label.lower():
            sib = span.find_next_sibling("span")
            if sib:
                return sib.get_text(strip=True)
    return ""


def _parse_address(addr):
    """Split 'street postal_code city country' from ORIAS into structured parts."""
    if not addr:
        return {"street": "", "postal_code": "", "city": ""}
    # Trim trailing 'France'
    addr = re.sub(r'\s+France\s*$', '', addr, flags=re.I).strip()
    # Postal code = 5 digits, city follows
    m = re.search(r'(.+?)\s+(\d{5})\s+(.+)', addr)
    if m:
        return {
            "street": m.group(1).strip(),
            "postal_code": m.group(2),
            "city": m.group(3).strip(),
        }
    return {"street": addr, "postal_code": "", "city": ""}


def enrich_member(member):
    """Fetch ORIAS detail page by SIREN and fill address/phone/email/orias_inscription_date.

    Mutates and returns the member dict.
    """
    siren = (member.get("siren") or "").strip()
    if not siren or len(siren) != 9:
        return member

    # Skip only if all target fields are already populated. orias_inscription_date
    # is part of the skip key so previously-enriched members get re-fetched on
    # backfills that add it.
    has_addr = (member.get("address") or {}).get("street")
    has_phone = member.get("phone")
    has_email = member.get("email")
    has_orias_date = member.get("orias_inscription_date")
    if has_addr and has_phone and has_email and has_orias_date:
        return member

    url = DETAIL_URL.format(siren=siren)
    try:
        resp = fetch(url, delay=0.7, max_retries=2)
        if not resp or resp.status_code != 200:
            return member
    except Exception as e:
        logger.debug(f"ORIAS detail fetch failed for {siren}: {e}")
        return member

    soup = BeautifulSoup(resp.text, "lxml")

    # Quick guard: did we land on a real fiche or a "not found" page?
    if not soup.find("span", class_="label"):
        return member

    # Address
    if not has_addr:
        addr_raw = _extract_label_value(soup, "Adresse")
        if addr_raw:
            parts = _parse_address(addr_raw)
            from config import DEPARTMENTS, DEPT_TO_REGION
            dept = extract_department(parts["postal_code"])
            member["address"] = {
                "street": parts["street"],
                "postal_code": parts["postal_code"],
                "city": parts["city"],
                "department": dept,
                "department_name": DEPARTMENTS.get(dept, ""),
                "region": DEPT_TO_REGION.get(dept, ""),
            }

    # Phone
    if not has_phone:
        phone_raw = _extract_label_value(soup, "Téléphone public")
        if phone_raw:
            cleaned = clean_phone(phone_raw)
            if cleaned:
                member["phone"] = cleaned

    # Email
    if not has_email:
        email_raw = _extract_label_value(soup, "Email public")
        if email_raw:
            # ORIAS HTML-encodes the @ as &#64; — BeautifulSoup decodes it,
            # but be defensive in case the encoding survives.
            email_raw = html_module.unescape(email_raw)
            cleaned = clean_email(email_raw)
            if cleaned:
                member["email"] = cleaned

    # ORIAS number (sometimes shown on the detail page)
    if not member.get("orias_number"):
        orias_raw = _extract_label_value(soup, "N° d'immatriculation")
        if orias_raw:
            digits = re.sub(r'\D', '', orias_raw)
            if len(digits) == 8:
                member["orias_number"] = digits

    # ORIAS first registration date — earliest "Inscrit le DD/MM/YYYY".
    # Some entities show "Supprimé le ..." for old/removed registrations; we
    # explicitly only take "Inscrit le" matches, then keep the oldest one
    # (= the year the cabinet first joined the registry).
    if not has_orias_date:
        all_text = soup.get_text(" ", strip=True)
        inscrit_dates = re.findall(r'Inscrit le (\d{2})/(\d{2})/(\d{4})', all_text)
        if inscrit_dates:
            # Convert to ISO yyyy-mm-dd, take the earliest
            iso_dates = [f"{y}-{mo}-{d}" for d, mo, y in inscrit_dates]
            member["orias_inscription_date"] = min(iso_dates)

    return member


def batch_enrich(members, max_lookups=None, log_every=50):
    """Enrich members from ORIAS detail pages by SIREN.

    `max_lookups=None` enriches everyone missing at least one of the 3 fields.
    """
    candidates = []
    for m in members:
        if not m.get("siren"):
            continue
        has_addr = (m.get("address") or {}).get("street")
        has_phone = m.get("phone")
        has_email = m.get("email")
        has_orias_date = m.get("orias_inscription_date")
        if has_addr and has_phone and has_email and has_orias_date:
            continue
        candidates.append(m)

    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"ORIAS detail: {len(candidates)} members to enrich")

    found_addr = found_phone = found_email = 0
    for i, m in enumerate(candidates, 1):
        before_addr = bool((m.get("address") or {}).get("street"))
        before_phone = bool(m.get("phone"))
        before_email = bool(m.get("email"))

        enrich_member(m)

        if not before_addr and (m.get("address") or {}).get("street"):
            found_addr += 1
        if not before_phone and m.get("phone"):
            found_phone += 1
        if not before_email and m.get("email"):
            found_email += 1

        if i % log_every == 0:
            logger.info(
                f"  ORIAS detail {i}/{len(candidates)} "
                f"(+{found_addr} addr, +{found_phone} phone, +{found_email} email)"
            )

    logger.info(
        f"ORIAS detail done: +{found_addr} addresses, +{found_phone} phones, "
        f"+{found_email} emails (out of {len(candidates)} lookups)"
    )
    return members
