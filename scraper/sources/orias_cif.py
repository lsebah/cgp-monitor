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
import re

import requests

from .base import (
    make_member_dict,
    clean_siren,
    extract_department,
)

logger = logging.getLogger(__name__)

# Official ORIAS publishable list of CIF (Conseillers en Investissements Financiers).
# This is the full registry, not an association subset.
DOWNLOAD_URLS = [
    "https://pro.orias.fr/download/conseiller_en_investissement_financier_CIF.xls",
    "https://www.orias.fr/download/conseiller_en_investissement_financier_CIF.xls",
]

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


def _download():
    """Download the ORIAS CIF file. Returns (content_bytes, content_type) or (None, None)."""
    for url in DOWNLOAD_URLS:
        try:
            logger.info(f"ORIAS CIF: downloading {url}")
            resp = requests.get(url, headers=HEADERS, timeout=60)
            if resp.status_code == 200 and resp.content:
                ctype = resp.headers.get("Content-Type", "")
                logger.info(f"ORIAS CIF: got {len(resp.content)} bytes ({ctype})")
                return resp.content, ctype
            logger.warning(f"ORIAS CIF: {url} -> HTTP {resp.status_code}")
        except requests.RequestException as e:
            logger.warning(f"ORIAS CIF: download failed for {url}: {e}")
    return None, None


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

    Option B filtering: only keep entries with usable prospection info
    (SIREN, address, or a direct contact), and skip clearly inactive records.

    Returns:
        List of normalized member dicts.
    """
    logger.info("Starting ORIAS CIF comprehensive import...")
    content, ctype = _download()
    if not content:
        logger.error("ORIAS CIF: download failed, no data imported")
        return []

    rows = _parse_rows(content, ctype)
    logger.info(f"ORIAS CIF: parsed {len(rows)} raw rows")

    members = []
    skipped_inactive = 0
    skipped_no_info = 0
    seen = set()

    for rec in rows:
        company_name = rec.get("company_name", "").strip()
        if not company_name or len(company_name) < 2:
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

        # Option B: require at least one usable prospection field
        # (SIREN lets us enrich later; address/postal code locates the firm).
        if not (siren or postal_code or address or city):
            skipped_no_info += 1
            continue

        # Dedup within this import
        key = siren or f"{company_name.lower()}|{city.lower()}"
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
        f"ORIAS CIF import complete: {len(members)} usable CGPs "
        f"({skipped_inactive} inactive, {skipped_no_info} without usable info skipped)"
    )
    return members
