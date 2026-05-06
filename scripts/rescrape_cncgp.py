"""
Targeted re-scrape of CNCGP only — uses the new direct-URL scraper to recover
the cabinets the form-submit version was silently dropping.

Why this isn't `python -m scraper.main`:
  - main.py re-scrapes ALL sources and runs detect_changes which overwrites
    existing members entirely (only first_seen/contacted are preserved).
    That would wipe creation_date / orias_inscription_date / phone / email
    enriched on ANACOFI yesterday.
  - Here we touch ONLY CNCGP rows and preserve every enrichment field on
    members that already exist.

Run:
    python scripts/rescrape_cncgp.py            # full re-scrape
    python scripts/rescrape_cncgp.py --depts 35,67,38   # smoke
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

from sources.cncgp import scrape_cncgp

DATA_DIR = os.path.join(REPO, "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Fields filled by post-scrape enrichers — never overwrite them with empty
# values from a fresh scrape.
ENRICHMENT_FIELDS = (
    "creation_date", "orias_inscription_date", "orias_number",
    "data_gouv",
)


def _smart_update(existing, fresh):
    """Update existing member in place with fresh-scraped fields, preserving
    every enrichment field already populated. Used when the same id appears
    in both."""
    # Fields the CNCGP scrape overwrites (these can change between runs)
    for key in ("company_name", "company_name_normalized", "phone",
                "website", "specialties"):
        if fresh.get(key):
            existing[key] = fresh[key]
    # Address: replace if scrape has a non-empty street
    fresh_addr = fresh.get("address") or {}
    if fresh_addr.get("street"):
        existing["address"] = fresh_addr
    # Activities: union (don't shrink)
    fresh_acts = set(fresh.get("activities") or [])
    cur_acts = set(existing.get("activities") or [])
    if fresh_acts:
        existing["activities"] = sorted(cur_acts | fresh_acts)
    # Directors: trust the fresh scrape if it has any (CNCGP exposes them)
    if fresh.get("directors"):
        # Preserve linkedin URLs we already had
        old_by_name = {
            (d.get("name") or "").lower(): d
            for d in (existing.get("directors") or [])
        }
        merged = []
        for d in fresh["directors"]:
            key = (d.get("name") or "").lower()
            old = old_by_name.get(key)
            if old:
                for k, v in old.items():
                    if k not in d and v:
                        d[k] = v
            merged.append(d)
        existing["directors"] = merged
    # Email: only fill if currently empty (scrape rarely has email anyway)
    if fresh.get("email") and not existing.get("email"):
        existing["email"] = fresh["email"]
    # Mark CNCGP membership
    existing.setdefault("associations", {})["cncgp"] = {"member": True}
    # Update last_seen
    existing["last_seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--depts", type=str, default=None,
                   help="Comma-separated dept codes (smoke test)")
    args = p.parse_args()

    depts = args.depts.split(",") if args.depts else None

    with open(MEMBERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    existing = data["members"]
    by_id = {m["id"]: m for m in existing}
    cncgp_before = sum(
        1 for m in existing if (m.get("associations") or {}).get("cncgp", {}).get("member")
    )
    logger.info(f"Loaded {len(existing)} members, {cncgp_before} already tagged CNCGP")

    fresh = scrape_cncgp(departments=depts)
    logger.info(f"CNCGP scrape returned {len(fresh)} cabinets")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    updated = 0
    added = 0
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

    cncgp_after = sum(
        1 for m in existing if (m.get("associations") or {}).get("cncgp", {}).get("member")
    )
    logger.info(
        f"DONE: {updated} CNCGP rows updated, {added} new cabinets added. "
        f"Total CNCGP: {cncgp_before} -> {cncgp_after}"
    )

    # Refresh top-level stats (so the UI tile counts stay correct)
    stats = data.setdefault("stats", {})
    by_assoc = stats.setdefault("by_association", {})
    by_assoc["cncgp"] = cncgp_after
    stats["total_members"] = len(existing)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    # Sort alphabetically (matches the main pipeline)
    existing.sort(key=lambda m: (m.get("company_name") or "").lower())
    data["members"] = existing

    with open(MEMBERS_PATH, "w", encoding="utf-8") as fout:
        json.dump(data, fout, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {MEMBERS_PATH}")


if __name__ == "__main__":
    main()
