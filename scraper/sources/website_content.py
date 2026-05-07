"""
Website content enricher for CGP cabinets.

Fetches the cabinet's homepage + a few canonical sub-pages (équipe / expertises
/ qui sommes-nous), extracts:
- Encours sous gestion (AUM) when stated on the site
- Expertises / domaines d'activité (gestion privée, immobilier, retraite, etc.)
- Mention de produits structurés (yes/no + keyword evidence)
- URL d'une page équipe
- Liens LinkedIn d'entreprise

Stored on the member as:
    member["website_data"] = {
        "homepage_status": 200,
        "fetched_at": "2026-05-07",
        "aum_eur": 250_000_000,           # if found
        "aum_evidence": "...250 millions...",
        "expertises": ["gestion-privee", "immobilier", "retraite"],
        "has_structured_products": True,
        "structured_evidence": "...autocall...",
        "structured_keywords": ["autocall", "produit structure"],
        "team_url": "https://...example.fr/notre-equipe",
        "linkedin_company_url": "https://linkedin.com/company/...",
        "pages_scanned": ["https://...", "https://.../equipe"],
    }

We intentionally avoid Playwright by default (too slow at 5000 cabinets) and
only fall back to it when the static HTML has no body text.
"""
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import fetch, fetch_browser

logger = logging.getLogger(__name__)

# --- Keyword dictionaries (case-insensitive, accents-stripped) ---

KEYWORDS_STRUCTURED = [
    "produit structure", "produits structures",
    "titres de creance structures", "titres structures",
    "autocall", "autocallable",
    "fonds a formule", "fonds a formules",
    "emtn", "euro medium term note",
    "structured notes", "obligations structurees",
    "produits derives complexes",
    "phoenix",
    "athena",
    "certificats indexes",
    "produits a capital protege",
]

# Map of expertise keywords → canonical tag
EXPERTISES_MAP = {
    "gestion-privee": ["gestion privee", "gestion de patrimoine", "gestion de fortune", "wealth"],
    "immobilier": ["immobilier", "scpi", "opci", "pierre papier", "investissement locatif"],
    "retraite": ["retraite", "per ", "perp ", "madelin", "preparation retraite"],
    "assurance-vie": ["assurance vie", "assurance-vie", "contrats av", "luxembourg"],
    "fiscalite": ["fiscalite", "optimisation fiscale", "defiscalisation", "ifi", "isf"],
    "succession": ["succession", "transmission", "donation", "demembrement"],
    "credit": ["credit", "iobsp", "courtage credit", "financement", "credit immobilier"],
    "entreprise": ["chef d entreprise", "dirigeant", "transmission entreprise",
                   "remuneration dirigeant", "pacte dutreil", "pacte d utreil"],
    "international": ["expatriation", "expatrie", "non resident", "international"],
    "esg": ["esg", "isr", "investissement responsable", "durable", "impact"],
    "private-equity": ["private equity", "fcpr", "fpci", "non cote"],
    "produits-structures": KEYWORDS_STRUCTURED,
}

# Sub-pages we look for, in priority order
TEAM_PATHS = [
    "/equipe", "/notre-equipe", "/l-equipe", "/team", "/qui-sommes-nous",
    "/le-cabinet", "/cabinet", "/a-propos", "/about",
]

EXPERTISE_PATHS = [
    "/expertises", "/expertise", "/nos-expertises", "/nos-services",
    "/services", "/metiers", "/notre-metier",
]

# Encours regex — matches French formulations like:
#   "1,2 milliards d'euros sous gestion"
#   "+ de 250 M€ d'encours"
#   "encours de 50 millions d'euros"
#   "AUM: 1.2 bn €"
ENCOURS_PATTERNS = [
    re.compile(
        r"(?:encours|sous\s+gestion|aum|assets\s+under\s+management|gerons?|"
        r"administrons?|geres?|geres\s+pour\s+le\s+compte|conseilles?)"
        r"[^.\n]{0,150}?"
        r"([\d]+(?:[\s .,]\d+)*)\s*"
        r"(milliard|md|md\s*€|millions?|m\s*€|m€|mds?|bn)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([\d]+(?:[\s .,]\d+)*)\s*"
        r"(milliard|md|md\s*€|millions?|m\s*€|m€|mds?|bn)\s*"
        r"(?:d['’]?\s*euros?\s*)?"
        r"(?:d['’]?\s*encours|sous\s+gestion|d['’]?aum|"
        r"geres|administres|conseilles)",
        re.IGNORECASE,
    ),
]


def _strip_accents(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_text(html_or_text: str) -> str:
    """Lowercase + strip accents; safe for keyword matching."""
    return _strip_accents(html_or_text.lower())


def _parse_amount(num_str: str, unit: str) -> int:
    """Convert "1,2" + "milliards" → 1_200_000_000."""
    s = num_str.strip().replace(" ", "").replace(" ", "")
    # French decimal: "1.234,56" or "1234,56" or "1.2"
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        # "1.234.567" (thousand separator)
        s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return 0
    u = unit.lower().strip()
    if u.startswith("milliard") or u.startswith("md") or u == "bn" or u.startswith("mds"):
        return int(n * 1_000_000_000)
    if u.startswith("million") or u.startswith("m"):
        return int(n * 1_000_000)
    return int(n)


def detect_encours(text: str):
    """Return (aum_eur, evidence) or (None, '')."""
    if not text:
        return None, ""
    for pat in ENCOURS_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        amount = _parse_amount(m.group(1), m.group(2))
        # Sanity: AUM for a CGP is ~1M€ to ~50Mds€
        if 1_000_000 <= amount <= 50_000_000_000:
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            evidence = re.sub(r"\s+", " ", text[start:end]).strip()[:200]
            return amount, evidence
    return None, ""


def detect_expertises(normalized_text: str):
    """Return list of expertise tags found in text."""
    found = []
    for tag, keywords in EXPERTISES_MAP.items():
        for kw in keywords:
            if kw in normalized_text:
                found.append(tag)
                break
    return found


def detect_structured(normalized_text: str):
    """Return (has_structured: bool, evidence: str, keywords: list)."""
    found = []
    for kw in KEYWORDS_STRUCTURED:
        if kw in normalized_text:
            found.append(kw)
    if not found:
        return False, "", []
    idx = normalized_text.find(found[0])
    snippet = normalized_text[max(0, idx - 80): idx + 160]
    snippet = re.sub(r"\s+", " ", snippet).strip()[:240]
    return True, snippet, found


def _safe_get(url: str, delay=2.0, timeout=15):
    """GET with retries; returns (status, html) or (None, '')."""
    try:
        resp = fetch(url, delay=delay, max_retries=2,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; CMF/1.0)"},
                     allow_redirects=True)
        if resp is None:
            return None, ""
        # Ensure utf-8 decoding even when server lies about encoding
        if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "windows-1252"):
            try:
                resp.encoding = resp.apparent_encoding or "utf-8"
            except Exception:
                pass
        return resp.status_code, resp.text or ""
    except Exception as e:
        logger.debug(f"  fetch {url} failed: {e}")
        return None, ""


def _extract_links(soup, base_url):
    """All <a href> on page, absolutized, deduped, http(s) only."""
    out = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        if full.startswith(("http://", "https://")):
            out.add(full.split("#")[0])
    return out


def _find_subpage(base_url, links, paths_priority):
    """Find the best matching internal sub-page from candidate paths."""
    base_host = urlparse(base_url).netloc.lower()
    same_host = [u for u in links if urlparse(u).netloc.lower() == base_host]
    for path in paths_priority:
        for url in same_host:
            p = urlparse(url).path.lower().rstrip("/")
            if p.endswith(path):
                return url
    # Fallback: any link whose path contains a TEAM keyword fragment
    for path in paths_priority:
        frag = path.strip("/").lower()
        for url in same_host:
            if frag in urlparse(url).path.lower():
                return url
    return None


def _find_linkedin_company(links):
    for url in links:
        if "linkedin.com/company/" in url.lower():
            return url
    return None


def enrich_website(member, max_pages=3, browser_fallback=False):
    """Scrape the cabinet's website. Mutates member['website_data']."""
    site = (member.get("website") or "").strip()
    if not site:
        return False
    if not site.startswith("http"):
        site = "http://" + site

    wd = {"fetched_at": datetime.utcnow().strftime("%Y-%m-%d"), "pages_scanned": []}

    status, html = _safe_get(site)
    wd["homepage_status"] = status
    wd["homepage_url"] = site

    if (not html or len(html) < 500) and browser_fallback:
        try:
            html = fetch_browser(site, wait_ms=2500)
            wd["homepage_status"] = 200 if html else status
            wd["used_browser"] = True
        except Exception as e:
            logger.debug(f"  browser fallback failed for {site}: {e}")

    if not html:
        member["website_data"] = wd
        return True  # Recorded the failure — counts as "tried"

    wd["pages_scanned"].append(site)

    soup = BeautifulSoup(html, "lxml") if html else None
    body_text = ""
    if soup:
        # Strip noisy tags before extracting visible text
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        body_text = soup.get_text(separator=" ", strip=True)

    pages_text = [body_text]

    if soup:
        all_links = _extract_links(soup, site)
        team = _find_subpage(site, all_links, TEAM_PATHS)
        if team:
            wd["team_url"] = team
        expertise = _find_subpage(site, all_links, EXPERTISE_PATHS)

        ln = _find_linkedin_company(all_links)
        if ln:
            wd["linkedin_company_url"] = ln

        # Fetch up to (max_pages-1) extra pages: expertise > team
        extras = []
        if expertise:
            extras.append(expertise)
        if team and team not in extras:
            extras.append(team)
        for url in extras[: max_pages - 1]:
            s2, h2 = _safe_get(url)
            if s2 and h2:
                wd["pages_scanned"].append(url)
                soup2 = BeautifulSoup(h2, "lxml")
                for t in soup2(["script", "style", "noscript"]):
                    t.decompose()
                pages_text.append(soup2.get_text(separator=" ", strip=True))

    full_text = "\n".join(pages_text)
    norm = _normalize_text(full_text)

    aum, ev = detect_encours(full_text)
    if aum:
        wd["aum_eur"] = aum
        wd["aum_evidence"] = ev

    wd["expertises"] = detect_expertises(norm)

    has_struct, ev_s, kw_s = detect_structured(norm)
    wd["has_structured_products"] = has_struct
    if has_struct:
        wd["structured_evidence"] = ev_s
        wd["structured_keywords"] = kw_s

    member["website_data"] = wd
    return True


def batch_enrich(members, max_lookups=None, log_every=20, checkpoint_fn=None,
                 checkpoint_every=50, only_missing=True):
    """Enrich all members that have a website but no website_data yet."""
    candidates = []
    for m in members:
        if not m.get("website"):
            continue
        if only_missing and m.get("website_data"):
            continue
        candidates.append(m)
    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"website content: {len(candidates)} candidates")
    found_aum = found_struct = found_team = 0
    for i, m in enumerate(candidates, 1):
        try:
            enrich_website(m)
        except Exception as e:
            logger.warning(f"  website enrich crashed for {m.get('company_name')}: {e}")
            continue
        wd = m.get("website_data") or {}
        if wd.get("aum_eur"):
            found_aum += 1
        if wd.get("has_structured_products"):
            found_struct += 1
        if wd.get("team_url"):
            found_team += 1
        if i % log_every == 0:
            logger.info(
                f"  website {i}/{len(candidates)} "
                f"(+{found_aum} AUMs, +{found_struct} structured, +{found_team} team_urls)"
            )
        if checkpoint_fn and (i % checkpoint_every == 0):
            try:
                checkpoint_fn()
                logger.info(f"  -> checkpoint saved at {i}/{len(candidates)}")
            except Exception as ex:
                logger.warning(f"  checkpoint failed: {ex}")
    logger.info(
        f"website content done: +{found_aum} AUMs, +{found_struct} structured, "
        f"+{found_team} team_urls"
    )
    return members
