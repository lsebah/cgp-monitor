"""
Contact enrichment: find website + email for members that don't have them.

Pipeline:
  1. website_finder.batch_find  -> fills `website` (via search engines)
  2. enricher.batch_enrich_emails -> extracts `email` from those websites

Newly-discovered / new / recently-created cabinets are enriched FIRST, so the
"new guys" get their coordonnées under their name as soon as they appear.
Capped per run (website discovery is slow + rate-limited); daily runs converge.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(__file__))

from sources.website_finder import batch_find
from sources.enricher import batch_enrich_emails
from detector import build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")

MAX_SITE_LOOKUPS = 120   # websites discovered per run (search engines are slow)
MAX_EMAIL_LOOKUPS = 400  # email extractions per run (parallel, faster)


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


def priority(m):
    """Lower = enriched first. New / discovered / recent cabinets come first."""
    assoc = m.get("associations") or {}
    discovered = ("manuel" in assoc) or ("registre" in assoc)
    if m.get("is_new") or discovered:
        return 0
    if m.get("creation_date"):
        return 1  # has a creation date -> sort by recency below
    return 2


def main():
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data = load_json(MEMBERS_PATH, {"members": [], "scrape_status": {}})
    members = data.get("members", [])
    logger.info(f"Loaded {len(members)} members")

    before_web = sum(1 for m in members if m.get("website"))
    before_email = sum(1 for m in members if m.get("email"))

    # Order members so the highest-priority (new/discovered/recent) come first;
    # batch_find/batch_enrich pick candidates in list order. Within a priority
    # bucket, most-recently-created first.
    members.sort(key=lambda m: (priority(m), _inv_date(m.get("creation_date") or "")))

    def checkpoint():
        data["members"] = members
        data["last_updated"] = now_iso
        save_json(data, MEMBERS_PATH)
        logger.info("  checkpoint saved")

    logger.info(">>> Finding websites...")
    batch_find(members, max_lookups=MAX_SITE_LOOKUPS,
               checkpoint_fn=checkpoint, checkpoint_every=40)

    logger.info(">>> Extracting emails from websites...")
    batch_enrich_emails(members, max_lookups=MAX_EMAIL_LOOKUPS)

    after_web = sum(1 for m in members if m.get("website"))
    after_email = sum(1 for m in members if m.get("email"))
    logger.info(f"Websites: {before_web} -> {after_web} (+{after_web - before_web})")
    logger.info(f"Emails:   {before_email} -> {after_email} (+{after_email - before_email})")

    # Restore canonical sort (by name) for stable diffs / display.
    members.sort(key=lambda m: m.get("company_name", "").lower())

    ss = data.get("scrape_status", {})
    ss["contacts_enrich"] = {"status": "success", "timestamp": now_iso,
                             "websites": after_web, "emails": after_email}
    stats = build_stats(members, 0, today)
    stats["scrape_status"] = ss
    stats["last_updated"] = now_iso

    data["members"] = members
    data["scrape_status"] = ss
    data["stats"] = stats
    data["last_updated"] = now_iso
    save_json(data, MEMBERS_PATH)
    save_json(stats, STATS_PATH)
    logger.info("Done.")


def _inv_date(iso):
    """Invert an ISO date so that, sorted ascending, the most recent comes first."""
    try:
        y, m, d = iso[:10].split("-")
        return f"{9999 - int(y):04d}-{12 - int(m):02d}-{31 - int(d):02d}"
    except Exception:
        return "zzzz"


if __name__ == "__main__":
    main()
