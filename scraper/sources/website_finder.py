"""
Website discovery for CGP cabinets that have no `website` field yet.

Strategy:
1. Try DuckDuckGo HTML search via Playwright with query
   '"<company_name>" <city> CGP'.
2. From the results, pick the first non-blacklisted host whose normalized
   domain shares a substantial token with the cabinet's company name.
3. Save as `member["website"]` and `member.setdefault("website_meta", {})
   ["discovered_via"] = "ddg"`.

Blacklisted hosts: directory aggregators (cncgp.fr, cncef.org, anacofi.asso.fr,
societe.com, pappers.fr, linkedin.com, infogreffe.fr, etc.) and social media.

Falls back to Bing if DDG is rate-limited (returns "anomaly" page).
"""
import logging
import re
import unicodedata
from urllib.parse import quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from .base import fetch_browser, normalize_name

logger = logging.getLogger(__name__)

BRAVE_URL = "https://search.brave.com/search?q="
DDG_URL = "https://html.duckduckgo.com/html/?q="
BING_URL = "https://www.bing.com/search?q="

BLACKLIST_HOSTS = {
    "cncgp.fr", "www.cncgp.fr",
    "cncef.org", "www.cncef.org",
    "anacofi.asso.fr", "www.anacofi.asso.fr",
    "ucgp.fr", "www.ucgp.fr",
    "affo.fr", "www.affo.fr",
    "orias.fr", "www.orias.fr",
    "societe.com", "www.societe.com",
    "pappers.fr", "www.pappers.fr",
    "infogreffe.fr", "www.infogreffe.fr",
    "verif.com", "www.verif.com",
    "data.gouv.fr",
    "linkedin.com", "fr.linkedin.com", "www.linkedin.com",
    "facebook.com", "www.facebook.com",
    "twitter.com", "x.com",
    "youtube.com", "www.youtube.com",
    "instagram.com", "www.instagram.com",
    "google.com", "www.google.com",
    "duckduckgo.com",
    "bing.com",
    "wikipedia.org", "fr.wikipedia.org",
    "leadersleague.com", "www.leadersleague.com",
    "decideurs-magazine.com",
    "argusdelassurance.com",
    "lefigaro.fr",
    "lesechos.fr",
    "boursorama.com",
    "challenges.fr",
    "verifsiren.com",
    "manageo.fr", "www.manageo.fr",
    "kompass.com",
    "europages.fr",
    "l-expert-comptable.com",
    "fiches-pratiques.chefdentreprise.com",
    "lexpansion.lexpress.fr",
    "blacktower-fr.com",
    "rubypayeur.com",
    "rocketreach.co",
    "infinance.fr", "www.infinance.fr",
    "entreprises.lefigaro.fr",
    "annuaire-cgp.fr",
    "patrimoine24.com",
    "cmes-conseils.fr",
    "fr.indeed.com", "indeed.com",
    "fr.glassdoor.com", "glassdoor.com",
    "dnb.com",
}

# When picking the best result we want at least ONE of these tokens to appear
# in the candidate domain — they're the most distinctive bits of the company
# name (drop legal suffixes and common stopwords).
GENERIC_TOKENS = {
    "patrimoine", "conseil", "conseils", "finance", "finances", "gestion",
    "investissement", "investissements", "groupe", "associes", "associates",
    "partners", "partenaires", "capital", "wealth", "advisor", "advisory",
    "cabinet", "consulting", "et", "and", "the", "le", "la", "les", "de",
    "du", "des", "in", "on", "by", "co", "company", "compagnie",
    "sas", "sarl", "eurl", "sa", "sasu", "sci",
}


def _norm_token(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _name_tokens(company_name: str):
    """Distinctive tokens of the company name (≥3 chars, not generic)."""
    n = normalize_name(company_name)
    return [t for t in n.split() if len(t) >= 3 and t not in GENERIC_TOKENS]


def _url_score(url: str, name_tokens, member_postal: str = "") -> int:
    """Higher = better. Penalize blacklisted hosts and PDFs."""
    p = urlparse(url)
    host = p.netloc.lower().lstrip("www.")
    if not host:
        return -100
    if host in BLACKLIST_HOSTS:
        return -100
    # Allow common subdomain only
    if host.startswith("www."):
        host_test = host[4:]
    else:
        host_test = host
    if host_test in BLACKLIST_HOSTS:
        return -100
    if url.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")):
        return -50
    score = 0
    domain = host.split(".")[0]
    domain_norm = _norm_token(domain)
    for t in name_tokens:
        tt = _norm_token(t)
        if not tt:
            continue
        if tt == domain_norm:
            score += 20
        elif tt in domain_norm:
            score += 10
        elif domain_norm in tt:
            score += 5
    # Soft bonus for .fr / .com
    if host.endswith(".fr"):
        score += 2
    elif host.endswith(".com"):
        score += 1
    return score


def _parse_brave_results(html: str):
    """Extract URLs from Brave Search HTML results, in displayed order."""
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    # Brave's snippet links are rendered as <a class="..."> inside #results
    for a in soup.select("#results a[href]"):
        href = a.get("href", "")
        if not href.startswith("http"):
            continue
        # Skip Brave's own internal links
        if "search.brave.com" in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append(href)
    return out


def _parse_ddg_results(html: str):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            out.append(unquote(m.group(1)))
        elif href.startswith("http"):
            out.append(href)
    return out


def _parse_bing_results(html: str):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("li.b_algo h2 a"):
        href = li.get("href", "")
        if href.startswith("http"):
            out.append(href)
    return out


def find_website(company_name: str, city: str = "", postal_code: str = "",
                 fallback_bing: bool = True) -> tuple:
    """Search Brave Search (then DDG / Bing) for the cabinet's website.

    Returns (url, source) where source is 'brave' / 'ddg' / 'bing'
    or ('', '') if no acceptable match was found.
    """
    if not company_name:
        return "", ""
    name_tokens = _name_tokens(company_name)
    if not name_tokens:
        return "", ""

    q_parts = [f'"{company_name}"']
    if city:
        q_parts.append(city)
    q_parts.append("CGP OR conseil OR patrimoine")
    query = " ".join(q_parts)

    # Brave Search is the primary engine — DDG is rate-limiting CGP's IP range
    # (banned 2026-05-06) and Bing returns a bot-challenge page in headless.
    urls = []
    source = ""
    try:
        html = fetch_browser(BRAVE_URL + quote_plus(query), wait_ms=2500)
        urls = _parse_brave_results(html) if html else []
        source = "brave"
    except Exception as e:
        logger.debug(f"Brave fetch failed: {e}")

    if not urls:
        try:
            html = fetch_browser(DDG_URL + quote_plus(query), wait_ms=2200)
            urls = _parse_ddg_results(html) if html else []
            source = "ddg"
        except Exception as e:
            logger.debug(f"DDG fetch failed: {e}")

    if not urls and fallback_bing:
        try:
            html2 = fetch_browser(BING_URL + quote_plus(query), wait_ms=2200)
            urls = _parse_bing_results(html2)
            source = "bing"
        except Exception as e:
            logger.debug(f"Bing fetch failed: {e}")

    if not urls:
        return "", ""

    scored = sorted(((u, _url_score(u, name_tokens, postal_code)) for u in urls),
                    key=lambda x: -x[1])
    best, score = scored[0]
    if score < 5:
        return "", ""
    # Normalize to root: search results often point to a deep page, but the
    # downstream content enricher will fan out from the homepage anyway.
    p = urlparse(best)
    root = f"{p.scheme}://{p.netloc}/"
    return root, source


def enrich_member(member):
    if member.get("website"):
        return False
    name = member.get("company_name") or ""
    addr = member.get("address") or {}
    url, source = find_website(name, city=addr.get("city", ""),
                                postal_code=addr.get("postal_code", ""))
    if not url:
        return False
    member["website"] = url
    member.setdefault("website_meta", {})["discovered_via"] = source
    return True


def batch_find(members, max_lookups=None, log_every=20, checkpoint_fn=None,
               checkpoint_every=100):
    """Discover websites for members that don't have one."""
    candidates = [m for m in members if not m.get("website") and m.get("company_name")]
    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"website finder: {len(candidates)} candidates")
    found = 0
    for i, m in enumerate(candidates, 1):
        try:
            if enrich_member(m):
                found += 1
        except Exception as e:
            logger.warning(f"  finder crashed for {m.get('company_name')}: {e}")
        if i % log_every == 0:
            logger.info(f"  finder {i}/{len(candidates)} (+{found} sites)")
        if checkpoint_fn and (i % checkpoint_every == 0):
            try:
                checkpoint_fn()
            except Exception as ex:
                logger.warning(f"  checkpoint failed: {ex}")
    logger.info(f"website finder done: +{found} new sites")
    return members
