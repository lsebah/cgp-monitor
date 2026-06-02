"""
ORIAS CIF Importer - Comprehensive import of the official ORIAS registry.

This is the authoritative, exhaustive source of ALL Conseillers en
Investissements Financiers (CIF) registered in France - including firms that
are NOT members of CNCGP / CNCEF / ANACOFI / AFFO and would otherwise be
missing from association-directory scrapers.

ORIAS publishes a downloadable "liste publiable" of registered CIF.
We download it, parse it (robust to xls / xlsx / html / csv formats), map the
columns flexibly, and keep only entries with usable prospection info
(option B: SIREN, address, or direct contact).
"""
import io
import logging
import os
import re
from urllib.parse import urljoin

import requests

from .base import (
    make_member_dict,
    clean_siren,
    extract_department,
)

logger = logging.getLogger(__name__)

# Official ORIAS publishable list of CIF (Conseillers en Investissements Financiers).
# This is the full registry, not an association subset. The download now requires
# an authenticated ORIAS pro session (see _authenticated_download).
DOWNLOAD_URLS = [
    "https://pro.orias.fr/download/conseiller_en_investissement_financier_CIF.xls",
    "https://www.orias.fr/download/conseiller_en_investissement_financier_CIF.xls",
]

# ORIAS pro login (Grails/Spring-Security). Credentials come from env vars
# (GitHub secrets) - never hard-coded, never logged.
LOGIN_PAGE_URL = "https://pro.orias.fr/login/auth"
DEFAULT_LOGIN_ACTION = "https://pro.orias.fr/login/authenticate"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/vnd.ms-excel, text/html, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Flexible header matching. ORIAS column labels vary over time / between exports,
# so we match on normalized keywords rather than exact strings.
COLUMN_PATTERNS = {
    "company_name": [r"d[eé]nomination", r"raison\s*sociale", r"nom\s*commercial", r"\bnom\b", r"libell[eé]"],
    "orias_number": [r"immatricul", r"enregistrement", r"n[°o]\s*orias", r"\borias\b"],
    "siren": [r"siren", r"siret"],
    "city": [r"\bville\b", r"commune", r"localit[eé]"],
    "postal_code": [r"code\s*postal", r"\bcp\b", r"postal"],
    "address": [r"adresse", r"voie", r"\brue\b"],
    "status": [r"statut", r"[eé]tat", r"inscription"],
}


def _normalize_header(h):
    h = str(h or "").strip().lower()
    # strip accents roughly
    for a, b in [("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ô", "o"), ("î", "i"), ("ç", "c")]:
        h = h.replace(a, b)
    return re.sub(r"\s+", " ", h)


def _map_columns(headers):
    """Map column index -> field name based on fuzzy header matching."""
    mapping = {}
    norm_headers = [_normalize_header(h) for h in headers]
    for idx, h in enumerate(norm_headers):
        for field, patterns in COLUMN_PATTERNS.items():
            if field in mapping.values():
                continue
            if any(re.search(p, h) for p in patterns):
                mapping[idx] = field
                break
    return mapping


def _looks_like_html(content, ctype):
    if "html" in (ctype or "").lower():
        return True
    head = content[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head


def _diagnose_html(content, url):
    """Log a snippet + any data-file / download links found in an HTML response."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "lxml")
        title = (soup.title.get_text(strip=True) if soup.title else "")[:120]
        logger.warning(f"ORIAS CIF: {url} returned HTML (title={title!r})")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"\.(xls|xlsx|csv)(\?|$)", href, re.I) or "download" in href.lower():
                links.append(href)
        # meta refresh redirect
        meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
        if meta and meta.get("content"):
            logger.warning(f"ORIAS CIF: meta refresh -> {meta['content']}")
        if links:
            logger.warning(f"ORIAS CIF: candidate data links: {links[:10]}")
        return links
    except Exception as e:
        logger.warning(f"ORIAS CIF: html diagnose failed: {e}")
        return []


def _fetch(url):
    return requests.get(url, headers=HEADERS, timeout=90, allow_redirects=True)


def _authenticated_download():
    """Log into the ORIAS pro portal and download the CIF file with that session.

    Credentials are read from the ORIAS_USERNAME / ORIAS_PASSWORD env vars
    (GitHub secrets). Returns (content, ctype) or (None, None). The password is
    never logged.
    """
    user = os.environ.get("ORIAS_USERNAME")
    pw = os.environ.get("ORIAS_PASSWORD")
    if not (user and pw):
        logger.info("ORIAS CIF: no ORIAS_USERNAME/ORIAS_PASSWORD set, skipping authenticated download")
        return None, None

    try:
        from bs4 import BeautifulSoup
        s = requests.Session()
        s.headers.update(HEADERS)

        # 1. Load the login page to obtain session cookie + form details.
        r = s.get(LOGIN_PAGE_URL, timeout=60)
        action = DEFAULT_LOGIN_ACTION
        data = {}
        user_field, pass_field = "username", "password"
        try:
            soup = BeautifulSoup(r.content, "lxml")
            form = soup.find("form")
            if form:
                if form.get("action"):
                    action = urljoin(r.url, form["action"])
                for inp in form.find_all("input"):
                    name = inp.get("name")
                    if not name:
                        continue
                    itype = (inp.get("type") or "text").lower()
                    if itype == "password":
                        pass_field = name
                    elif itype in ("text", "email") and inp.get("type") != "submit":
                        # first text-like field is the username/login
                        if user_field == "username":
                            user_field = name
                    elif itype == "hidden":
                        data[name] = inp.get("value", "")
        except Exception as e:
            logger.warning(f"ORIAS CIF: login form parse failed, using defaults: {e}")

        data[user_field] = user
        data[pass_field] = pw
        logger.info(f"ORIAS CIF: submitting login to {action} (user field '{user_field}')")

        # 2. Authenticate.
        s.post(action, data=data, timeout=60, allow_redirects=True)

        # 3. Download with the authenticated session.
        for url in DOWNLOAD_URLS:
            r3 = s.get(url, timeout=90, allow_redirects=True)
            if r3.status_code == 200 and r3.content and not _looks_like_html(
                r3.content, r3.headers.get("Content-Type", "")
            ):
                logger.info(f"ORIAS CIF: authenticated download OK ({len(r3.content)} bytes) from {url}")
                return r3.content, r3.headers.get("Content-Type", "")
            logger.warning(f"ORIAS CIF: authenticated GET {url} still not a file "
                           f"(status {r3.status_code}, ends at {r3.url})")
        logger.error("ORIAS CIF: login appears to have failed (download still gated)")
    except Exception as e:
        logger.error(f"ORIAS CIF: authenticated download error: {e}")
    return None, None


def _download():
    """Download the ORIAS CIF file. Returns (content_bytes, content_type) or (None, None).

    Order: authenticated pro download (if credentials set) -> public URLs
    (rejecting HTML/login pages) -> data.gouv.fr fallback.
    """
    content, ctype = _authenticated_download()
    if content:
        return content, ctype

    for url in DOWNLOAD_URLS:
        try:
            logger.info(f"ORIAS CIF: downloading {url}")
            resp = _fetch(url)
            if resp.status_code != 200 or not resp.content:
                logger.warning(f"ORIAS CIF: {url} -> HTTP {resp.status_code}")
                continue
            ctype = resp.headers.get("Content-Type", "")
            logger.info(f"ORIAS CIF: got {len(resp.content)} bytes ({ctype}) from {resp.url}")
            if not _looks_like_html(resp.content, ctype):
                return resp.content, ctype
            # HTML: diagnose and try to follow an embedded data-file link
            for href in _diagnose_html(resp.content, url):
                target = urljoin(resp.url, href)
                try:
                    r2 = _fetch(target)
                    if r2.status_code == 200 and r2.content and not _looks_like_html(
                        r2.content, r2.headers.get("Content-Type", "")
                    ):
                        logger.info(f"ORIAS CIF: followed link -> {target} ({len(r2.content)} bytes)")
                        return r2.content, r2.headers.get("Content-Type", "")
                except requests.RequestException:
                    continue
        except requests.RequestException as e:
            logger.warning(f"ORIAS CIF: download failed for {url}: {e}")

    # Fallback: discover an ORIAS CSV/XLS resource on data.gouv.fr dynamically.
    content = _download_from_datagouv()
    if content:
        return content, "datagouv"
    return None, None


# data.gouv.fr open-data API. Used as a fallback source: we search for any
# ORIAS / "intermediaires" dataset and download its first CSV/XLS resource.
DATAGOUV_API = "https://www.data.gouv.fr/api/1/datasets/"
DATAGOUV_QUERIES = ["orias", "intermediaires assurance banque finance", "conseiller investissement financier"]


def _download_from_datagouv():
    """Search data.gouv.fr for an ORIAS intermediaries dataset and download a CSV/XLS resource."""
    for q in DATAGOUV_QUERIES:
        try:
            logger.info(f"ORIAS CIF: data.gouv.fr fallback search '{q}'")
            resp = requests.get(DATAGOUV_API, params={"q": q, "page_size": 20},
                                headers=HEADERS, timeout=40)
            if resp.status_code != 200:
                logger.warning(f"data.gouv.fr search '{q}' -> HTTP {resp.status_code}")
                continue
            data = resp.json()
            for ds in data.get("data", []):
                title = (ds.get("title") or "").lower()
                if "orias" not in title and "intermediaire" not in title and "intermédiaire" not in title:
                    continue
                for res in ds.get("resources", []):
                    fmt = (res.get("format") or "").lower()
                    rtitle = (res.get("title") or "").lower()
                    url = res.get("url")
                    if not url:
                        continue
                    # Prefer CIF / financial-investment resources, else any tabular file.
                    if fmt in ("csv", "xls", "xlsx") or any(
                        e in url.lower() for e in (".csv", ".xls", ".xlsx")
                    ):
                        logger.info(f"ORIAS CIF: data.gouv.fr resource '{rtitle}' ({fmt}) {url}")
                        r2 = requests.get(url, headers=HEADERS, timeout=90)
                        if r2.status_code == 200 and r2.content:
                            logger.info(f"ORIAS CIF: data.gouv.fr download {len(r2.content)} bytes")
                            return r2.content
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"data.gouv.fr fallback '{q}' failed: {e}")
    return None


def _parse_rows(content, ctype):
    """
    Parse the downloaded content into a list of row dicts {field: value}.
    Robust to xls / xlsx / html-table / csv formats.
    """
    # Strategy 1: pandas (handles xls via xlrd, xlsx via openpyxl)
    try:
        import pandas as pd
        for engine in ("xlrd", "openpyxl", None):
            try:
                df = pd.read_excel(io.BytesIO(content), engine=engine, dtype=str)
                return _df_to_rows(df)
            except Exception:
                continue
    except ImportError:
        logger.warning("ORIAS CIF: pandas not available")

    # Strategy 2: HTML table (ORIAS sometimes serves an HTML table as .xls)
    head = content[:512].lstrip().lower()
    if b"<html" in head or b"<table" in head or "html" in (ctype or "").lower():
        try:
            import pandas as pd
            tables = pd.read_html(io.BytesIO(content))
            if tables:
                return _df_to_rows(max(tables, key=len))
        except Exception as e:
            logger.debug(f"ORIAS CIF: html parse failed: {e}")

    # Strategy 3: CSV fallback
    try:
        import csv
        text = content.decode("utf-8", errors="replace")
        sample = text[:2000]
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = list(reader)
        if rows:
            return _table_to_rows(rows)
    except Exception as e:
        logger.debug(f"ORIAS CIF: csv parse failed: {e}")

    logger.error("ORIAS CIF: could not parse downloaded file in any known format")
    return []


def _df_to_rows(df):
    headers = list(df.columns)
    table = [headers] + df.astype(str).values.tolist()
    return _table_to_rows(table)


def _table_to_rows(table):
    """Given a list-of-lists (first row = headers), return list of field dicts."""
    if not table or len(table) < 2:
        return []
    # Find the header row: the one whose cells match the most known patterns.
    best_idx, best_map = 0, {}
    for i in range(min(5, len(table))):
        m = _map_columns(table[i])
        if len(m) > len(best_map):
            best_idx, best_map = i, m
    if not best_map:
        logger.warning("ORIAS CIF: no recognizable columns found in header")
        return []

    logger.info(f"ORIAS CIF: column map = {best_map} (header row {best_idx})")
    rows = []
    for raw in table[best_idx + 1:]:
        rec = {}
        for idx, field in best_map.items():
            if idx < len(raw):
                val = str(raw[idx]).strip()
                if val and val.lower() not in ("nan", "none"):
                    rec[field] = val
        if rec:
            rows.append(rec)
    return rows


def scrape_orias_cif():
    """
    Import all registered CIF from the official ORIAS publishable list.

    Option A: import EVERY active CIF, even cabinets that have no email / phone
    / SIREN / address yet. Missing contact details are enriched later
    (batch_enrich_emails) and on subsequent scrapes. Only clearly inactive
    (radie / supprime) records and rows with no usable name are skipped.

    Returns:
        List of normalized member dicts.
    """
    logger.info("Starting ORIAS CIF comprehensive import (option A: import all)...")
    content, ctype = _download()
    if not content:
        logger.error("ORIAS CIF: download failed, no data imported")
        return []

    rows = _parse_rows(content, ctype)
    logger.info(f"ORIAS CIF: parsed {len(rows)} raw rows")

    members = []
    skipped_inactive = 0
    skipped_no_name = 0
    seen = set()

    for rec in rows:
        company_name = rec.get("company_name", "").strip()
        if not company_name or len(company_name) < 2:
            skipped_no_name += 1
            continue

        # Skip clearly inactive registrations
        status = rec.get("status", "").lower()
        if re.search(r"(radi[ée]|supprim|inactif|cess|d[eé]missionn)", status):
            skipped_inactive += 1
            continue

        siren = clean_siren(rec.get("siren", ""))
        postal_code = rec.get("postal_code", "")
        city = rec.get("city", "")
        address = rec.get("address", "")
        orias_number = ""
        m = re.search(r"\b(\d{8})\b", rec.get("orias_number", ""))
        if m:
            orias_number = m.group(1)

        # Option A: no contact/SIREN/address requirement - import everything.

        # Dedup within this import (by SIREN, ORIAS number, or name+city)
        key = siren or orias_number or f"{company_name.lower()}|{city.lower()}"
        if key in seen:
            continue
        seen.add(key)

        member = make_member_dict(
            company_name=company_name,
            siren=siren,
            orias_number=orias_number,
            address_street=address,
            postal_code=postal_code,
            city=city,
            activities=["CIF"],
            source="orias",
            source_url="https://pro.orias.fr/",
        )
        members.append(member)

    logger.info(
        f"ORIAS CIF import complete: {len(members)} CGPs imported "
        f"({skipped_inactive} inactive, {skipped_no_name} without a name skipped)"
    )
    return members
