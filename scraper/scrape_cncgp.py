"""
Fast CNCGP-only scrape.

Scrapes CNCGP (Playwright headless browser), merges additively into existing
member data. Requires Playwright + Chromium. Runs in ~30-45 min.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

from sources.cncgp import scrape_cncgp
from merger import merge_all_sources
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

    logger.info(">>> Scraping CNCGP (Playwright)...")
    t0 = datetime.now(timezone.utc)
    try:
        cncgp = scrape_cncgp()
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.info(f"<<< CNCGP done in {elapsed:.0f}s ({len(cncgp)} members)")
        scrape_status["cncgp"] = {"status": "success", "count": len(cncgp),
                                  "timestamp": now_iso, "duration_s": round(elapsed, 1)}
    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
        logger.error(f"<<< CNCGP FAILED after {elapsed:.0f}s: {e}")
        scrape_status["cncgp"] = {"status": "error", "error": str(e),
                                  "timestamp": now_iso, "duration_s": round(elapsed, 1)}
        return

    logger.info(">>> Merging...")
    all_members = merge_all_sources(existing_members, cncgp)

    logger.info(">>> Detecting changes...")
    all_members, new_members = detect_changes(existing_members, all_members, today)

    stats = build_stats(all_members, len(new_members), today)
    active = [m for m in all_members if m.get("status") != "removed"]
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
