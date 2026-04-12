"""
CGP Monitor - Base scraper utilities.
Shared HTTP session, rate limiting, normalization helpers.
"""
import hashlib
import logging
import re
import time
import unicodedata

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html, application/json, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Rate limiting
_last_request_time = 0
MIN_DELAY = 1.0  # seconds between requests


def rate_limit(delay=None):
    """Enforce minimum delay between requests."""
    global _last_request_time
    d = delay or MIN_DELAY
    elapsed = time.time() - _last_request_time
    if elapsed < d:
        time.sleep(d - elapsed)
    _last_request_time = time.time()


def fetch(url, method="GET", max_retries=3, delay=None, **kwargs):
    """Fetch URL with rate limiting and retries."""
    rate_limit(delay)
    for attempt in range(max_retries):
        try:
            if method.upper() == "POST":
                resp = SESSION.post(url, timeout=20, **kwargs)
            else:
                resp = SESSION.get(url, timeout=20, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries} failed for {url}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return None


def make_member_id(company_name: str, siren: str = "", city: str = "") -> str:
    """Generate a stable unique ID for a CGP member."""
    if siren and len(siren) >= 9:
        raw = f"siren|{siren[:9]}"
    else:
        raw = f"name|{normalize_name(company_name)}|{normalize_city(city)}"
    return "cgp_" + hashlib.md5(raw.encode()).hexdigest()[:10]


def normalize_name(name: str) -> str:
    """Normalize company name for matching."""
    if not name:
        return ""
    name = strip_accents(name.lower().strip())
    # Remove common legal suffixes
    for suffix in ["sas", "sarl", "eurl", "sa", "sasu", "sci", "scp",
                   "selarl", "selurl", "selafa", "selas"]:
        name = re.sub(rf'\b{suffix}\b', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)
    return name.strip()


def normalize_city(city: str) -> str:
    """Normalize city name for matching."""
    if not city:
        return ""
    return strip_accents(city.lower().strip())


def strip_accents(text: str) -> str:
    """Remove diacritics from text."""
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def clean_siren(siren: str) -> str:
    """Clean and validate SIREN number (9 digits)."""
    if not siren:
        return ""
    cleaned = re.sub(r'[\s\-\.]', '', str(siren))
    if re.match(r'^\d{9,14}$', cleaned):
        return cleaned[:9]  # SIREN is first 9 digits of SIRET
    return ""


def clean_phone(phone: str) -> str:
    """Clean phone number."""
    if not phone:
        return ""
    cleaned = re.sub(r'[^\d+]', '', phone.strip())
    if cleaned and not cleaned.startswith('+'):
        if cleaned.startswith('0') and len(cleaned) == 10:
            cleaned = '+33' + cleaned[1:]
    return cleaned


def clean_email(email: str) -> str:
    """Clean and validate email."""
    if not email:
        return ""
    email = email.strip().lower()
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return email
    return ""


def extract_department(postal_code: str) -> str:
    """Extract department code from postal code."""
    if not postal_code:
        return ""
    pc = postal_code.strip()
    if len(pc) >= 2:
        # Overseas departments
        if pc.startswith("97"):
            return pc[:3]
        # Corsica
        if pc.startswith("20"):
            code = int(pc[:5]) if len(pc) >= 5 else int(pc)
            return "2A" if code < 20200 else "2B"
        return pc[:2]
    return ""


def make_member_dict(
    company_name="",
    siren="",
    orias_number="",
    address_street="",
    postal_code="",
    city="",
    phone="",
    email="",
    website="",
    activities=None,
    specialties=None,
    directors=None,
    source="",
    source_url="",
):
    """Create a normalized member dictionary."""
    dept = extract_department(postal_code)
    siren = clean_siren(siren)

    from config import DEPARTMENTS, DEPT_TO_REGION

    member = {
        "id": make_member_id(company_name, siren, city),
        "company_name": company_name.strip(),
        "company_name_normalized": normalize_name(company_name),
        "siren": siren,
        "orias_number": orias_number.strip() if orias_number else "",
        "address": {
            "street": address_street.strip() if address_street else "",
            "postal_code": postal_code.strip() if postal_code else "",
            "city": city.strip() if city else "",
            "department": dept,
            "department_name": DEPARTMENTS.get(dept, ""),
            "region": DEPT_TO_REGION.get(dept, ""),
        },
        "phone": clean_phone(phone),
        "email": clean_email(email),
        "website": website.strip() if website else "",
        "activities": activities or [],
        "specialties": specialties or [],
        "directors": directors or [],
        "associations": {},
        "groupement": None,
        "first_seen": "",
        "last_seen": "",
        "is_new": False,
        "source_urls": {},
    }

    if source:
        member["associations"][source] = {"member": True}
        if source_url:
            member["source_urls"][source] = source_url

    return member
