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
    m.setdefault("associations", {})["manuel"] = {"member": True}  # protected from affiliation filter
    m["first_seen"] = today
    m["last_seen"] = today
    m["is_new"] = True
    return m


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    log.info(f"Discovering NAF {NAF} firms created since {cutoff} (last {DAYS} days)")

    data = json.load(open(MEMBERS_PATH, encoding="utf-8"))
    members = data.get("members", [])
    seen_siren = {m.get("siren") for m in members if m.get("siren")}
    seen_nc = {(m.get("company_name_normalized") or normalize_name(m.get("company_name", "")),
                normalize_city(m.get("address", {}).get("city", ""))) for m in members}

    new_members = []
    page = 1
    while True:
        j = api_get({
            "activite_principale": NAF,
            "etat_administratif": "A",
            "date_creation_min": cutoff,
            "page": page,
            "per_page": 25,
        })
        time.sleep(0.25)
        if not j:
            break
        results = j.get("results", [])
        if not results:
            break
        for e in results:
            cd = e.get("date_creation") or ""
            if cd < cutoff:                      # safety: enforce window client-side
                continue
            siren = e.get("siren") or ""
            name = e.get("nom_complet") or e.get("nom_raison_sociale") or ""
            if not name:
                continue
            nn = normalize_name(name)
            city = normalize_city((e.get("siege") or {}).get("libelle_commune") or "")
            if siren in seen_siren or (nn, city) in seen_nc:
                continue
            m = build_member(e, today)
            members.append(m)
            new_members.append(m)
            if siren:
                seen_siren.add(siren)
            seen_nc.add((nn, city))
            log.info(f"  + {m['company_name']} (créé {cd}, SIREN {siren or '-'}, "
                     f"{len(m['directors'])} dirigeant(s))")
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
