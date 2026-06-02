"""
Fast ORIAS-only discovery update.

The full scrape (scraper/main.py) never completes within the GitHub Actions
timeout, so ORIAS-discovered cabinets would never land. This lightweight job:

  1. imports the official ORIAS CIF registry (the exhaustive list of all
     Conseillers en Investissements Financiers, association member or not),
  2. merges it ADDITIVELY into the existing member data - tagging firms that
     are also ORIAS-registered and appending brand-new ones, never dropping or
     removing existing members,
  3. enriches the ORIAS-touched cabinets (data.gouv + ORIAS detail) up to a
     bounded cap so newly discovered firms gain SIREN / address / phone / email
     and become visible in the directory (which hides contact-less cabinets).
     Remaining cabinets are progressively enriched on subsequent daily runs.
  4. recomputes stats and the new-members feed.

This is purely additive and safe to run daily.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

from sources.orias_cif import scrape_orias_cif
from sources.base import normalize_name, normalize_city
from sources.recherche_entreprises import batch_enrich as batch_enrich_datagouv
from sources.orias_detail import batch_enrich as batch_enrich_orias
from detector import build_new_members_data, build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
NEW_MEMBERS_PATH = os.path.join(DATA_DIR, "new_members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")

# Bound enrichment so the job finishes well inside the workflow timeout.
# Newly discovered cabinets not reached this run are picked up on the next.
ENRICH_CAP = 400


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    existing_data = load_json(MEMBERS_PATH, {"members": [], "scrape_status": {}})
    existing_members = existing_data.get("members", [])
    existing_ids = {m["id"] for m in existing_members}
    logger.info(f"Loaded {len(existing_members)} existing members")

    # 1. Import the full official ORIAS CIF registry
    orias_members = scrape_orias_cif()
    if not orias_members:
        logger.error("ORIAS import returned 0 members - aborting (keeping existing data)")
        return

    # 2. Additive merge: index existing members, then for each ORIAS firm either
    #    tag an existing match or append it as new. We deliberately do NOT re-run
    #    the global merger (it fuzzy-merges existing members against each other
    #    and would silently drop distinct entries).
    siren_index = {}
    name_city_index = {}
    for m in existing_members:
        s = m.get("siren")
        if s:
            siren_index[s] = m
        nn = m.get("company_name_normalized") or normalize_name(m.get("company_name", ""))
        city = normalize_city(m.get("address", {}).get("city", ""))
        if nn:
            name_city_index[(nn, city)] = m

    merged = list(existing_members)
    new_members = []
    orias_touched = []
    matched = 0

    for o in orias_members:
        s = o.get("siren")
        nn = o.get("company_name_normalized") or normalize_name(o.get("company_name", ""))
        city = normalize_city(o.get("address", {}).get("city", ""))

        match = None
        if s and s in siren_index:
            match = siren_index[s]
        elif nn and (nn, city) in name_city_index:
            match = name_city_index[(nn, city)]

        if match:
            match.setdefault("associations", {})["orias"] = {"member": True}
            if not match.get("orias_number") and o.get("orias_number"):
                match["orias_number"] = o["orias_number"]
            match["activities"] = list(set(match.get("activities", [])) | set(o.get("activities", [])))
            orias_touched.append(match)
            matched += 1
        else:
            o["first_seen"] = today
            o["last_seen"] = today
            o["is_new"] = True
            merged.append(o)
            new_members.append(o)
            orias_touched.append(o)
            if s:
                siren_index[s] = o
            if nn:
                name_city_index[(nn, city)] = o

    for m in existing_members:
        m["is_new"] = False
        m.setdefault("last_seen", today)

    added = len(new_members)
    logger.info(
        f"ORIAS update: {len(orias_members)} ORIAS rows -> {added} brand-new firms added, "
        f"{matched} matched existing, {len(merged)} total (was {len(existing_members)})"
    )

    # 3. Enrich ORIAS-touched cabinets (bounded) so new firms gain a SIREN /
    #    address / phone / email and become visible in the directory.
    try:
        logger.info(f"Enriching ORIAS cabinets via data.gouv (cap {ENRICH_CAP})...")
        batch_enrich_datagouv(orias_touched, max_lookups=ENRICH_CAP)
    except Exception as e:
        logger.error(f"data.gouv enrichment failed: {e}")
    try:
        logger.info(f"Enriching ORIAS cabinets via ORIAS detail (cap {ENRICH_CAP})...")
        batch_enrich_orias(orias_touched, max_lookups=ENRICH_CAP)
    except Exception as e:
        logger.error(f"ORIAS detail enrichment failed: {e}")

    # 4. Recompute stats + new feed
    stats = build_stats(merged, len(new_members), today)
    scrape_status = existing_data.get("scrape_status", {})
    scrape_status["orias_discovery"] = {
        "status": "success", "count": len(orias_members),
        "added": added, "matched": matched, "timestamp": now_iso,
    }

    active = [m for m in merged if m.get("status") != "removed"]
    active.sort(key=lambda m: m.get("company_name", "").lower())

    save_json({
        "last_updated": now_iso,
        "scrape_status": scrape_status,
        "stats": stats,
        "members": active,
    }, MEMBERS_PATH)
    logger.info(f"Saved {len(active)} members")

    save_json(build_new_members_data(new_members, today), NEW_MEMBERS_PATH)
    stats["scrape_status"] = scrape_status
    stats["last_updated"] = now_iso
    save_json(stats, STATS_PATH)

    logger.info(f"Done. Total CGPs now: {stats['total_members']} (+{added} via ORIAS discovery)")


if __name__ == "__main__":
    main()
