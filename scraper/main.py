"""
CGP Monitor - Main scraper orchestrator.
Scrapes association directories, merges, detects new members, outputs JSON.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from sources.cncef import scrape_cncef
from sources.cncgp import scrape_cncgp
from sources.anacofi import scrape_anacofi
from sources.affo import scrape_affo
from sources.enricher import batch_enrich_emails
from merger import merge_all_sources
from detector import detect_changes, build_new_members_data, build_stats
from folk_export import export_new_members_csv
from config import GROUPEMENTS, ASSOCIATIONS

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
NEW_MEMBERS_PATH = os.path.join(DATA_DIR, "new_members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
GROUPEMENTS_PATH = os.path.join(DATA_DIR, "groupements.json")


def load_existing_data():
    """Load existing member data."""
    try:
        with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_updated": None, "members": [], "scrape_status": {}, "stats": {}}


def save_json(data, path):
    """Save data to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    logger.info("=" * 60)
    logger.info("CGP MONITOR - Starting scrape")
    logger.info("=" * 60)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    # Load existing data
    existing_data = load_existing_data()
    existing_members = existing_data.get("members", [])

    # Scrape all sources
    scrape_status = {}
    source_results = []

    # --- CNCEF ---
    logger.info(">>> Scraping CNCEF...")
    try:
        cncef_members = scrape_cncef(max_pages=500, enrich_details=False)
        source_results.append(cncef_members)
        scrape_status["cncef"] = {
            "status": "success",
            "count": len(cncef_members),
            "timestamp": now_iso,
        }
    except Exception as e:
        logger.error(f"CNCEF scrape failed: {e}")
        scrape_status["cncef"] = {"status": "error", "error": str(e), "timestamp": now_iso}

    # --- CNCGP ---
    logger.info(">>> Scraping CNCGP...")
    try:
        cncgp_members = scrape_cncgp(enrich_details=True)
        source_results.append(cncgp_members)
        scrape_status["cncgp"] = {
            "status": "success",
            "count": len(cncgp_members),
            "timestamp": now_iso,
        }
    except Exception as e:
        logger.error(f"CNCGP scrape failed: {e}")
        scrape_status["cncgp"] = {"status": "error", "error": str(e), "timestamp": now_iso}

    # --- ANACOFI ---
    logger.info(">>> Scraping ANACOFI...")
    try:
        anacofi_members = scrape_anacofi()
        source_results.append(anacofi_members)
        scrape_status["anacofi"] = {
            "status": "success",
            "count": len(anacofi_members),
            "timestamp": now_iso,
        }
    except Exception as e:
        logger.error(f"ANACOFI scrape failed: {e}")
        scrape_status["anacofi"] = {"status": "error", "error": str(e), "timestamp": now_iso}

    # --- AFFO ---
    logger.info(">>> Scraping AFFO...")
    try:
        affo_members = scrape_affo()
        source_results.append(affo_members)
        scrape_status["affo"] = {
            "status": "success",
            "count": len(affo_members),
            "timestamp": now_iso,
        }
    except Exception as e:
        logger.error(f"AFFO scrape failed: {e}")
        scrape_status["affo"] = {"status": "error", "error": str(e), "timestamp": now_iso}

    # Merge all sources
    logger.info(">>> Merging sources...")
    merged_members = merge_all_sources(*source_results)

    # Enrich emails from company websites
    logger.info(">>> Enriching emails from websites...")
    merged_members = batch_enrich_emails(merged_members, max_lookups=200)

    # Detect new members
    logger.info(">>> Detecting new members...")
    all_members, new_members = detect_changes(existing_members, merged_members, today)

    # Build stats
    stats = build_stats(all_members, len(new_members), today)

    # Sort by company name
    active_members = [m for m in all_members if m.get("status") != "removed"]
    active_members.sort(key=lambda m: m.get("company_name", "").lower())

    # Save members.json
    output = {
        "last_updated": now_iso,
        "scrape_status": scrape_status,
        "stats": stats,
        "members": active_members,
    }
    save_json(output, MEMBERS_PATH)
    logger.info(f"Saved {len(active_members)} members to {MEMBERS_PATH}")

    # Save new_members.json
    new_data = build_new_members_data(new_members, today)
    save_json(new_data, NEW_MEMBERS_PATH)
    logger.info(f"Saved {len(new_members)} new members to {NEW_MEMBERS_PATH}")

    # Save stats.json
    stats["scrape_status"] = scrape_status
    stats["last_updated"] = now_iso
    save_json(stats, STATS_PATH)

    # Save groupements.json
    save_json({
        "groupements": GROUPEMENTS,
        "associations": ASSOCIATIONS,
        "last_updated": now_iso,
    }, GROUPEMENTS_PATH)

    # Export new members to Folk CSV
    if new_members:
        csv_path = export_new_members_csv(new_members)
        if csv_path:
            logger.info(f"Folk CSV export: {csv_path}")

    logger.info(f"Done! {len(active_members)} total, {len(new_members)} new today")
    return output


if __name__ == "__main__":
    main()
