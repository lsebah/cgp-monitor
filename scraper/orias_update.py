"""
Fast ORIAS-only update.

The full scrape (CNCEF ~500 pages + CNCGP Playwright + email enrichment) never
finishes inside the GitHub Actions timeout. This lightweight job imports the
official ORIAS CIF registry, merges it into the EXISTING member data (keeping
every association member and their contacts), recomputes stats, and saves -
all in a couple of minutes.

It never marks existing members as removed: it is purely additive.
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
from detector import build_new_members_data, build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
NEW_MEMBERS_PATH = os.path.join(DATA_DIR, "new_members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")


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
    #    tag an existing match with the 'orias' association or append it as new.
    #    We deliberately do NOT re-run the global merger, which would fuzzy-merge
    #    existing members against each other and silently drop distinct entries.
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
            # Tag existing firm as also ORIAS-registered; fill only missing fields.
            match.setdefault("associations", {})["orias"] = {"member": True}
            if not match.get("orias_number") and o.get("orias_number"):
                match["orias_number"] = o["orias_number"]
            acts = set(match.get("activities", [])) | set(o.get("activities", []))
            match["activities"] = list(acts)
            matched += 1
        else:
            o["first_seen"] = today
            o["last_seen"] = today
            o["is_new"] = True
            merged.append(o)
            new_members.append(o)
            # index it so duplicate ORIAS rows don't double-add
            if s:
                siren_index[s] = o
            if nn:
                name_city_index[(nn, city)] = o

    # Preserve first_seen / flags for existing members
    for m in existing_members:
        m["is_new"] = False
        m.setdefault("last_seen", today)

    added = len(new_members)
    logger.info(
        f"ORIAS update: {len(orias_members)} ORIAS rows -> "
        f"{added} brand-new firms added, {matched} matched existing, "
        f"{len(merged)} total (was {len(existing_members)})"
    )

    # 4. Recompute stats + new-members feed
    stats = build_stats(merged, len(new_members), today)

    scrape_status = existing_data.get("scrape_status", {})
    scrape_status["orias"] = {"status": "success", "count": len(orias_members), "timestamp": now_iso}

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

    logger.info(f"Done. Total CGPs now: {stats['total_members']} (+{added} via ORIAS)")


if __name__ == "__main__":
    main()
