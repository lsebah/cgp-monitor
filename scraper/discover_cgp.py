"""
Discover CGP firms automatically from the OFFICIAL open company registry
(recherche-entreprises.api.gouv.fr) - no ORIAS, no login.

CGP / financial-advisory firms carry NAF code 66.19B (confirmed on known
cabinets). We page through every active 66.19B company, department by
department (to stay under the API's 10k pagination cap), and additively add
the ones not already in the directory, with verified data (SIREN, address,
dirigeants -> visible). Purely additive; never removes anything. Bounded per
run (CAP); the daily schedule extends coverage over time.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

import requests
from sources.base import make_member_dict, normalize_name, normalize_city
from sources.recherche_entreprises import _format_director
from config import DEPARTMENTS
from detector import build_new_members_data, build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
NEW_MEMBERS_PATH = os.path.join(DATA_DIR, "new_members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
API_URL = "https://recherche-entreprises.api.gouv.fr/search"
HEADERS = {"User-Agent": "cgp-monitor/1.0"}

NAF = "66.19B"                                  # CGP / auxiliary financial services
CAP = int(os.environ.get("CAP", "6000"))        # max new firms added per run
DELAY = float(os.environ.get("DELAY", "0.25"))  # politeness between API calls


def api_get(params):
    for attempt in range(4):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            time.sleep(1 + attempt)
    return None


def build_member(e):
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
    return m


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    data = json.load(open(MEMBERS_PATH, encoding="utf-8"))
    members = data.get("members", [])
    seen_siren = {m.get("siren") for m in members if m.get("siren")}
    seen_nc = set()
    for m in members:
        nn = m.get("company_name_normalized") or normalize_name(m.get("company_name", ""))
        seen_nc.add((nn, normalize_city(m.get("address", {}).get("city", ""))))
    log.info(f"Loaded {len(members)} existing members ({len(seen_siren)} with SIREN)")

    new_members = []
    for dept in sorted(DEPARTMENTS.keys()):
        if len(new_members) >= CAP:
            log.info(f"Reached CAP {CAP}, stopping.")
            break
        page = 1
        dept_added = 0
        while True:
            j = api_get({
                "activite_principale": NAF,
                "departement": dept,
                "etat_administratif": "A",
                "page": page,
                "per_page": 25,
            })
            time.sleep(DELAY)
            if not j:
                break
            results = j.get("results", [])
            if not results:
                break
            for e in results:
                siren = e.get("siren") or ""
                name = e.get("nom_complet") or e.get("nom_raison_sociale") or ""
                if not name:
                    continue
                nn = normalize_name(name)
                city = normalize_city((e.get("siege") or {}).get("libelle_commune") or "")
                if siren in seen_siren or (nn, city) in seen_nc:
                    continue
                m = build_member(e)
                m["first_seen"] = today
                m["last_seen"] = today
                m["is_new"] = True
                members.append(m)
                new_members.append(m)
                if siren:
                    seen_siren.add(siren)
                seen_nc.add((nn, city))
                dept_added += 1
                if len(new_members) >= CAP:
                    break
            total_pages = j.get("total_pages") or 1
            if page >= total_pages or len(new_members) >= CAP:
                break
            page += 1
        if dept_added:
            log.info(f"  dept {dept} ({DEPARTMENTS[dept]}): +{dept_added} new (running total {len(new_members)})")

    if not new_members:
        log.warning("No new CGP firms discovered this run.")
        return

    for m in members:
        m.setdefault("is_new", False)
        m.setdefault("last_seen", today)
    members.sort(key=lambda m: m.get("company_name", "").lower())

    stats = build_stats(members, len(new_members), today)
    ss = data.get("scrape_status", {})
    ss["registre_naf_discovery"] = {"status": "success", "added": len(new_members),
                                    "naf": NAF, "timestamp": now_iso}
    json.dump({"last_updated": now_iso, "scrape_status": ss, "stats": stats, "members": members},
              open(MEMBERS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(build_new_members_data(new_members, today), open(NEW_MEMBERS_PATH, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    stats["scrape_status"] = ss
    stats["last_updated"] = now_iso
    json.dump(stats, open(STATS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log.info(f"Done. Discovered {len(new_members)} new CGP firms. Total now {stats['total_members']}.")


if __name__ == "__main__":
    main()
