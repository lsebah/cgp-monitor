"""
Run UCGP groupement scrapers and smart-merge into members.json without
overwriting enrichment fields on existing cabinets.

Same pattern as rescrape_cncgp.py / rescrape_cncef.py.

Run:
    python scripts/scrape_ucgp.py            # all implemented groupements
    python scripts/scrape_ucgp.py --groupement entrepreneurs  # one only
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = os.path.join(REPO, "scraper")
sys.path.insert(0, SCRAPER)

from sources.ucgp import (
    scrape_entrepreneurs,
    scrape_finindep,
    scrape_actualis,
)

DATA_DIR = os.path.join(REPO, "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GROUPEMENTS = {
    "entrepreneurs": ("Le Club des Entrepreneurs CGP", scrape_entrepreneurs),
    "finindep":      ("Finindep",                       scrape_finindep),
    "actualis":      ("Actualis Associes",              scrape_actualis),
}

# Fields that scrape OUTPUT may NOT overwrite on existing rows
ENRICHMENT_FIELDS = (
    "creation_date", "orias_inscription_date", "orias_number",
    "data_gouv", "siren", "directors", "phone", "email", "website",
)


def _smart_update(existing, fresh):
    """Update existing cabinet with fresh-scraped fields, preserving every
    field already populated."""
    # Only fill blanks for the heavy fields
    for k in ENRICHMENT_FIELDS:
        if not existing.get(k) and fresh.get(k):
            existing[k] = fresh[k]
    # Address: only fill if existing has no street
    cur_addr = existing.get("address") or {}
    fresh_addr = fresh.get("address") or {}
    if not cur_addr.get("street") and fresh_addr.get("street"):
        existing["address"] = fresh_addr
    # Specialties: union (don't shrink)
    s = set(existing.get("specialties") or []) | set(fresh.get("specialties") or [])
    if s:
        existing["specialties"] = sorted(s)
    # Activities: union
    a = set(existing.get("activities") or []) | set(fresh.get("activities") or [])
    if a:
        existing["activities"] = sorted(a)
    # Tag UCGP membership + groupement
    existing.setdefault("associations", {})["ucgp"] = (
        fresh.get("associations") or {}
    ).get("ucgp", {"member": True})
    if fresh.get("groupement") and not existing.get("groupement"):
        existing["groupement"] = fresh["groupement"]
    # Track source URL
    existing.setdefault("source_urls", {}).update(fresh.get("source_urls") or {})
    existing["last_seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--groupement", choices=list(GROUPEMENTS.keys()) + ["all"],
                   default="all")
    args = p.parse_args()

    targets = (list(GROUPEMENTS.keys()) if args.groupement == "all"
               else [args.groupement])

    with open(MEMBERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    existing = data["members"]
    by_id = {m["id"]: m for m in existing}
    by_name = {(m.get("company_name_normalized") or "").strip(): m for m in existing}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_added = 0
    total_updated = 0

    for code in targets:
        label, fn = GROUPEMENTS[code]
        logger.info(f"=== Scraping {label} ===")
        try:
            fresh = fn()
        except Exception as e:
            logger.error(f"  scraper crashed: {e}")
            continue

        added = updated = 0
        for m in fresh:
            existing_member = by_id.get(m["id"])
            if not existing_member:
                # Try fuzzy name match (cabinet may already be in CNCGP/CNCEF)
                existing_member = by_name.get(m.get("company_name_normalized", ""))
            if existing_member:
                _smart_update(existing_member, m)
                updated += 1
            else:
                m["first_seen"] = today
                m["last_seen"] = today
                m["is_new"] = True
                existing.append(m)
                by_id[m["id"]] = m
                by_name[m.get("company_name_normalized", "")] = m
                added += 1

        logger.info(f"  -> {label}: +{added} new, {updated} updated")
        total_added += added
        total_updated += updated

    # Refresh aggregated stats
    ucgp_count = sum(
        1 for m in existing if (m.get("associations") or {}).get("ucgp", {}).get("member")
    )
    stats = data.setdefault("stats", {})
    stats.setdefault("by_association", {})["ucgp"] = ucgp_count
    stats["total_members"] = len(existing)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    existing.sort(key=lambda m: (m.get("company_name") or "").lower())
    data["members"] = existing

    with open(MEMBERS_PATH, "w", encoding="utf-8") as fout:
        json.dump(data, fout, indent=2, ensure_ascii=False)

    logger.info("=" * 60)
    logger.info(f"UCGP DONE: +{total_added} new, {total_updated} updated. "
                f"Total UCGP cabinets: {ucgp_count}")
    logger.info(f"Wrote {MEMBERS_PATH}")


if __name__ == "__main__":
    main()
