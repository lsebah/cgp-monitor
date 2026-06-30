"""
Discover RECENTLY CREATED CGP firms from the official open registry
(recherche-entreprises.api.gouv.fr) - no ORIAS, no login.

The "Créés < N jours" tile is the heart of a veille: catch newly registered
CGP cabinets early. CGP firms carry NAF 66.19B. We query every active 66.19B
company created in the last DAYS days and additively add the missing ones with
verified data (SIREN, address, dirigeants -> visible). They are tagged
'manuel' so a "keep only CNCGP/CNCEF/ANACOFI" filter never drops them.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

import requests
from sources.base import make_member_dict, normalize_name, normalize_city
from sources.recherche_entreprises import _format_director
from detector import build_new_members_data, build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
NEW_MEMBERS_PATH = os.path.join(DATA_DIR, "new_members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
API_URL = "https://recherche-entreprises.api.gouv.fr/search"
HEADERS = {"User-Agent": "cgp-monitor/1.0"}

NAF = "66.19B"
DAYS = int(os.environ.get("DAYS", "7"))


def api_get(params):
    for attempt in range(4):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                log.warning(f"API HTTP {r.status_code}: {r.text[:200]}")
                return None
            return r.json()
        except Exception as e:
            log.warning(f"API error: {e}")
            time.sleep(1 + attempt)
    return None


# Activity terms that signal a genuine CGP / courtage / financial-advisory firm.
# A brand-new NAF 66.19B registration is only kept if its name (denomination or
# enseigne) contains one of these — this drops the noise the registry is full of:
# bare individual auto-entrepreneurs, personal holdings (X CAPITAL / X PARTNERS),
# SCI, associations and unrelated companies that merely picked the 66.19B code.
CGP_KEYWORDS = (
    "PATRIMO", "CONSEIL", "GESTION", "FINANCE", "FINANCI", "INVESTISSEMENT",
    "COURTAGE", "COURTIER", "ASSURANCE", "EPARGNE", "PLACEMENT", "ALLOCATION",
    "FORTUNE", "WEALTH", "ASSET", "FAMILY OFFICE", "PRIVE", "PRIVEE",
    "EXPERTISE", "CABINET", "STRATEG", "FINANCIAL",
)
# Hard excludes even if a keyword sneaks in.
EXCLUDE_PATTERNS = ("ASSOCIATION ", "SCI ", "SYNDIC", " SCPI", "FONDS ",
                    "FONCIERE", "IMMOBILIERE", "HOLDING", "PARTICIPATION")


def looks_like_cgp(name):
    """True if the firm name looks like a real CGP/courtage/advisory cabinet."""
    n = (name or "").upper()
    if not n:
        return False
    if any(p in n for p in EXCLUDE_PATTERNS):
        return False
    return any(k in n for k in CGP_KEYWORDS)


# INSEE legal-form (categorie juridique) prefixes that are NOT operating CGP
# cabinets — objective exclusion on top of the name heuristic:
#   65xx = societes civiles (SCI, SCP civile, etc.)
#   69xx = groupements / GIE
# (Holdings have no dedicated legal form, so the NAF check elsewhere covers those.)
EXCLUDE_NJ_PREFIX = ("65", "69")


def excluded_legal_form(nature_juridique):
    nj = str(nature_juridique or "")
    return nj[:2] in EXCLUDE_NJ_PREFIX


def build_member(e, today):
    siege = e.get("siege") or {}
    directors = [d for d in (_format_director(x) for x in (e.get("dirigeants") or [])) if d]
    m = make_member_dict(
        company_name=e.get("nom_complet") or e.get("nom_raison_sociale") or "",
        siren=e.get("siren") or "",
        address_street=siege.get("adresse") or "",
        postal_code=siege.get("code_postal") or "",
        city=siege.get("libelle_commune") or "",
        activities=["CIF"],
        directors=directors,
        source="registre",
        source_url="https://annuaire-entreprises.data.gouv.fr/entreprise/" + (e.get("siren") or ""),
    )
    if e.get("date_creation"):
        m["creation_date"] = e["date_creation"]
    if e.get("nature_juridique"):
        m.setdefault("data_gouv", {})["nature_juridique"] = e["nature_juridique"]
    m.setdefault("associations", {})["manuel"] = {"member": True}  # protected from affiliation filter
    m["first_seen"] = today
    m["last_seen"] = today
    m["is_new"] = True
    return m


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()
    dt_cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)
    cutoff = dt_cutoff.strftime("%Y-%m-%d")          # ISO, for client-side compare
    cutoff_api = dt_cutoff.strftime("%d-%m-%Y")      # API expects DD-MM-YYYY
    log.info(f"Discovering NAF {NAF} firms created since {cutoff} (last {DAYS} days)")

    data = json.load(open(MEMBERS_PATH, encoding="utf-8"))
    members = data.get("members", [])
    seen_siren = {m.get("siren") for m in members if m.get("siren")}
    seen_nc = {(m.get("company_name_normalized") or normalize_name(m.get("company_name", "")),
                normalize_city(m.get("address", {}).get("city", ""))) for m in members}

    new_members = []

    def consider(e):
        cd = e.get("date_creation") or ""
        if cd < cutoff:                      # too old: outside the window
            return
        if cd > today:                       # post-dated (future): data.gouv anomaly
            return
        siren = e.get("siren") or ""
        name = e.get("nom_complet") or e.get("nom_raison_sociale") or ""
        if not name:
            return
        # Quality gate 1 (objective): drop civil-society / GIE legal forms.
        if excluded_legal_form(e.get("nature_juridique")):
            return
        # Quality gate 2 (name): keep only names that look like a real CGP
        # cabinet, not bare individuals / holdings / SCI / associations.
        if not looks_like_cgp(name):
            return
        nn = normalize_name(name)
        city = normalize_city((e.get("siege") or {}).get("libelle_commune") or "")
        if siren in seen_siren or (nn, city) in seen_nc:
            return
        m = build_member(e, today)
        members.append(m)
        new_members.append(m)
        if siren:
            seen_siren.add(siren)
        seen_nc.add((nn, city))
        log.info(f"  + {m['company_name']} (créé {cd}, SIREN {siren or '-'}, "
                 f"{len(m['directors'])} dirigeant(s))")

    # 1. Fast path: national query with the date filter (DD-MM-YYYY).
    probe = api_get({"activite_principale": NAF, "etat_administratif": "A",
                     "date_creation_min": cutoff_api, "page": 1, "per_page": 25})
    total = (probe or {}).get("total_results", 0)
    log.info(f"National date-filtered query total_results = {total}")

    if probe and 0 < total <= 3000:
        # Filter honoured -> paginate the filtered set.
        pages = min(probe.get("total_pages") or 1, 400)
        for e in probe.get("results", []):
            consider(e)
        for page in range(2, pages + 1):
            j = api_get({"activite_principale": NAF, "etat_administratif": "A",
                         "date_creation_min": cutoff_api, "page": page, "per_page": 25})
            time.sleep(0.2)
            if not j or not j.get("results"):
                break
            for e in j["results"]:
                consider(e)
    else:
        # Filter ignored -> reliable fallback: scan 66.19B department by department,
        # keeping only firms created within the window.
        log.info("Date filter not honoured; falling back to per-department scan.")
        from config import DEPARTMENTS
        for dept in sorted(DEPARTMENTS.keys()):
            page = 1
            while True:
                j = api_get({"activite_principale": NAF, "etat_administratif": "A",
                             "departement": dept, "page": page, "per_page": 25})
                time.sleep(0.2)
                if not j or not j.get("results"):
                    break
                for e in j["results"]:
                    consider(e)
                if page >= (j.get("total_pages") or 1):
                    break
                page += 1

    if not new_members:
        log.warning("No recently-created CGP firms missing. Nothing added.")
        return

    for m in members:
        m.setdefault("is_new", False)
        m.setdefault("last_seen", today)
    members.sort(key=lambda m: m.get("company_name", "").lower())

    stats = build_stats(members, len(new_members), today)
    ss = data.get("scrape_status", {})
    ss["registre_recent"] = {"status": "success", "added": len(new_members),
                             "days": DAYS, "timestamp": now_iso}
    json.dump({"last_updated": now_iso, "scrape_status": ss, "stats": stats, "members": members},
              open(MEMBERS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(build_new_members_data(new_members, today), open(NEW_MEMBERS_PATH, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    stats["scrape_status"] = ss
    stats["last_updated"] = now_iso
    json.dump(stats, open(STATS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log.info(f"Done. Added {len(new_members)} recently-created CGP firms. Total now {stats['total_members']}.")


if __name__ == "__main__":
    main()
