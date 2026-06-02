"""
Add named CGP firms to the directory using the OFFICIAL open company registry
(recherche-entreprises.api.gouv.fr) - no login, no ORIAS.

Given a list of firm names (env FIRMS, comma-separated, or the default below),
look each up, log the candidates, and additively add confident matches to
members.json with verified data (SIREN, address, dirigeants). Directors make
them visible in the directory. Purely additive; never removes anything.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

import requests
from sources.base import make_member_dict, normalize_name, normalize_city
from sources.recherche_entreprises import _format_director, _norm
from detector import build_new_members_data, build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
NEW_MEMBERS_PATH = os.path.join(DATA_DIR, "new_members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
API_URL = "https://recherche-entreprises.api.gouv.fr/search"
HEADERS = {"User-Agent": "cgp-monitor/1.0"}

DEFAULT_FIRMS = ["Alpy Gestion", "Blackwell Family Office"]


def lookup(name):
    try:
        r = requests.get(API_URL, params={"q": name, "per_page": 10}, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            log.warning(f"  API HTTP {r.status_code} for {name!r}")
            return []
        return r.json().get("results", [])
    except Exception as e:
        log.warning(f"  API error for {name!r}: {e}")
        return []


def build_member(entry):
    siege = entry.get("siege") or {}
    directors = []
    for d in (entry.get("dirigeants") or []):
        fd = _format_director(d)
        if fd:
            directors.append(fd)
    m = make_member_dict(
        company_name=entry.get("nom_complet") or entry.get("nom_raison_sociale") or "",
        siren=entry.get("siren") or "",
        address_street=siege.get("adresse") or "",
        postal_code=siege.get("code_postal") or "",
        city=siege.get("libelle_commune") or "",
        activities=["CIF"],
        directors=directors,
        source="registre",
        source_url="https://annuaire-entreprises.data.gouv.fr/entreprise/" + (entry.get("siren") or ""),
    )
    cd = entry.get("date_creation")
    if cd:
        m["creation_date"] = cd
    # Tag as a manually-requested firm so it survives any future
    # "keep only CNCGP/CNCEF/ANACOFI" affiliation filter.
    m.setdefault("associations", {})["manuel"] = {"member": True}
    return m


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()
    firms = [s.strip() for s in os.environ.get("FIRMS", "").split(",") if s.strip()] or DEFAULT_FIRMS
    log.info(f"Firms to add: {firms}")

    data = json.load(open(MEMBERS_PATH, encoding="utf-8"))
    members = data.get("members", [])
    by_siren = {m.get("siren"): m for m in members if m.get("siren")}
    by_namecity = {}
    for m in members:
        nn = m.get("company_name_normalized") or normalize_name(m.get("company_name", ""))
        by_namecity[(nn, normalize_city(m.get("address", {}).get("city", "")))] = m

    added, new_members = [], []
    for name in firms:
        log.info(f"=== {name} ===")
        results = lookup(name)
        if not results:
            log.warning(f"  no API result - SKIPPED (give me a more precise name or the city)")
            continue
        for e in results[:8]:
            log.info(f"  candidate: {e.get('nom_complet')} | SIREN {e.get('siren')} "
                     f"| NAF {e.get('activite_principale')} | {(e.get('siege') or {}).get('libelle_commune')}")
        # confident match: normalized name equal
        target = _norm(name)
        match = next((e for e in results if _norm(e.get("nom_complet") or e.get("nom_raison_sociale") or "") == target), None)
        if not match:
            match = next((e for e in results if target in _norm(e.get("nom_complet") or "")), None)
        if not match:
            log.warning(f"  no confident name match - SKIPPED (candidates logged above)")
            continue
        if match.get("siren") in by_siren:
            log.info(f"  already in directory (SIREN {match.get('siren')}) - skipped")
            continue
        m = build_member(match)
        m["first_seen"] = today
        m["last_seen"] = today
        m["is_new"] = True
        members.append(m)
        added.append(m)
        new_members.append(m)
        log.info(f"  ADDED: {m['company_name']} (SIREN {m['siren']}, {len(m['directors'])} dirigeant(s))")

    if not added:
        log.warning("Nothing added. (Verify names / provide city or SIREN.)")
        return

    for m in members:
        m.setdefault("is_new", False)
        m.setdefault("last_seen", today)
    members.sort(key=lambda m: m.get("company_name", "").lower())

    stats = build_stats(members, len(new_members), today)
    ss = data.get("scrape_status", {})
    ss["registre_manual"] = {"status": "success", "added": len(added), "timestamp": now_iso}
    json.dump({"last_updated": now_iso, "scrape_status": ss, "stats": stats, "members": members},
              open(MEMBERS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(build_new_members_data(new_members, today), open(NEW_MEMBERS_PATH, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    stats["scrape_status"] = ss
    stats["last_updated"] = now_iso
    json.dump(stats, open(STATS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log.info(f"Done. Added {len(added)} firm(s). Total now {stats['total_members']}.")


if __name__ == "__main__":
    main()
