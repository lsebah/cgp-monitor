"""
Targeted re-scrape of CNCEF to recover the cabinets the previous run dropped.

The CNCEF source has ~404 pages × 12 cards ≈ 4848 cabinets in the public
annuaire, but the last pipeline run only landed 180 (timed out / network
flaked while iterating). Same pattern as rescrape_cncgp.py: do not touch
ANACOFI / CNCGP enrichments.

Run:
    python scripts/rescrape_cncef.py
    python scripts/rescrape_cncef.py --max-pages 50  # smoke

Detail-page enrichment (phone/email/director) is intentionally skipped here
— the data.gouv SIREN-by-name lookup + ORIAS detail enricher (run via
backfill_anacofi.py --associations cncef afterwards) is faster and gives
better coverage than the per-cabinet detail page.
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

from sources.cncef import scrape_cncef

DATA_DIR = os.path.join(REPO, "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _smart_update(existing, fresh):
    """Update existing CNCEF row in place with fresh-scraped fields,
    preserving every enrichment field already populated."""
    for key in ("company_name", "company_name_normalized", "specialties"):
        if fresh.get(key):
            existing[key] = fresh[key]
    fresh_addr = fresh.get("address") or {}
    if fresh_addr.get("city"):
        # CNCEF gives us city + dept; only overwrite if the existing one
        # has neither street nor city (true "blank" row).
        cur_addr = existing.get("address") or {}
        if not cur_addr.get("street") and not cur_addr.get("city"):
            existing["address"] = fresh_addr
        else:
            # at least lift the dept/region if our existing row missed them
            for k in ("department", "department_name", "region"):
                if not cur_addr.get(k) and fresh_addr.get(k):
                    cur_addr[k] = fresh_addr[k]
            existing["address"] = cur_addr
    fresh_acts = set(fresh.get("activities") or [])
    cur_acts = set(existing.get("activities") or [])
    if fresh_acts:
        existing["activities"] = sorted(cur_acts | fresh_acts)
    existing.setdefault("associations", {})["cncef"] = {"member": True}
    if fresh.get("source_urls"):
        existing.setdefault("source_urls", {}).update(fresh["source_urls"])
    existing["last_seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-pages", type=int, default=500)
    args = p.parse_args()

    with open(MEMBERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    existing = data["members"]
    by_id = {m["id"]: m for m in existing}
    cncef_before = sum(
        1 for m in existing if (m.get("associations") or {}).get("cncef", {}).get("member")
    )
    logger.info(f"Loaded {len(existing)} members, {cncef_before} already tagged CNCEF")

    fresh = scrape_cncef(max_pages=args.max_pages, enrich_details=False)
    logger.info(f"CNCEF scrape returned {len(fresh)} cabinets")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated = added = 0
    for f in fresh:
        existing_member = by_id.get(f["id"])
        if existing_member:
            _smart_update(existing_member, f)
            updated += 1
        else:
            f["first_seen"] = today
            f["last_seen"] = today
            f["is_new"] = True
            existing.append(f)
            by_id[f["id"]] = f
            added += 1

    cncef_after = sum(
        1 for m in existing if (m.get("associations") or {}).get("cncef", {}).get("member")
    )
    logger.info(
        f"DONE: {updated} CNCEF rows updated, {added} new cabinets added. "
        f"Total CNCEF: {cncef_before} -> {cncef_after}"
    )

    stats = data.setdefault("stats", {})
    by_assoc = stats.setdefault("by_association", {})
    by_assoc["cncef"] = cncef_after
    stats["total_members"] = len(existing)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    existing.sort(key=lambda m: (m.get("company_name") or "").lower())
    data["members"] = existing

    with open(MEMBERS_PATH, "w", encoding="utf-8") as fout:
        json.dump(data, fout, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {MEMBERS_PATH}")


if __name__ == "__main__":
    main()
