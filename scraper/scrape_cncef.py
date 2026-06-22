"""
Fast CNCEF-only scrape.

Scrapes CNCEF (HTML paginated directory), then does a FAST additive merge
(by SIREN or normalized name+city — no fuzzy matching). Much faster than
merge_all_sources which does O(N²) SequenceMatcher on 10k+ members.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

from sources.cncef import scrape_cncef
from sources.base import normalize_name, normalize_city
from detector import detect_changes, build_new_members_data, build_stats

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
    logger.info(f"Loaded {len(existing_members)} existing members")

    scrape_status = existing_data.get("scrape_status", {})

    logger.info(">>> Scraping CNCEF...")
    t0 = datetime.now(timezone.utc)
    try:
        cncef = scrape_cncef(max_pages=500, enrich_details=False)
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.info(f"<<< CNCEF done in {elapsed:.0f}s ({len(cncef)} members)")
        scrape_status["cncef"] = {"status": "success", "count": len(cncef),
                                  "timestamp": now_iso, "duration_s": round(elapsed, 1)}
    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.error(f"<<< CNCEF FAILED after {elapsed:.0f}s: {e}")
        scrape_status["cncef"] = {"status": "error", "error": str(e),
                                  "timestamp": now_iso, "duration_s": round(elapsed, 1)}
        return

    # Fast additive merge (no fuzzy matching — O(N) instead of O(N²))
    logger.info(">>> Fast additive merge...")
    t1 = datetime.now(timezone.utc)
    siren_idx = {}
    name_city_idx = {}
    for m in existing_members:
        s = m.get("siren")
        if s:
            siren_idx[s] = m
        nn = m.get("company_name_normalized") or normalize_name(m.get("company_name", ""))
        city = normalize_city(m.get("address", {}).get("city", ""))
        if nn:
            name_city_idx[(nn, city)] = m

    new_members = []
    matched = 0
    for o in cncef:
        s = o.get("siren")
        nn = o.get("company_name_normalized") or normalize_name(o.get("company_name", ""))
        city = normalize_city(o.get("address", {}).get("city", ""))

        match = None
        if s and s in siren_idx:
            match = siren_idx[s]
        elif nn and (nn, city) in name_city_idx:
            match = name_city_idx[(nn, city)]

        if match:
            match.setdefault("associations", {})["cncef"] = o.get("associations", {}).get("cncef", {"member": True})
            acts = set(match.get("activities", [])) | set(o.get("activities", []))
            match["activities"] = list(acts)
            match["last_seen"] = today
            matched += 1
        else:
            o["first_seen"] = today
            o["last_seen"] = today
            o["is_new"] = True
            existing_members.append(o)
            new_members.append(o)
            if s:
                siren_idx[s] = o
            if nn:
                name_city_idx[(nn, city)] = o

    for m in existing_members:
        m.setdefault("is_new", False)
        m.setdefault("last_seen", today)

    merge_elapsed = (datetime.now(timezone.utc) - t1).total_seconds()
    logger.info(f"<<< Merge done in {merge_elapsed:.0f}s: {matched} matched, {len(new_members)} new")

    stats = build_stats(existing_members, len(new_members), today)
    active = [m for m in existing_members if m.get("status") != "removed"]
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

    logger.info(f"Done. Total: {len(active)}, New: {len(new_members)}")


if __name__ == "__main__":
    main()
