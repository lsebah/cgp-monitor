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
from sources.recherche_entreprises import batch_enrich as batch_enrich_datagouv
from sources.orias_detail import batch_enrich as batch_enrich_orias
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

    def run_source(name, fn, *args, **kwargs):
        """Wrap a source scrape with timing + uniform status reporting.

        Logs duration so a stalled source is obvious in CI logs (the workflow
        was being killed at 180min without any signal of which step ate it).
        """
        logger.info(f">>> Scraping {name.upper()}...")
        t0 = datetime.now(timezone.utc)
        try:
            members = fn(*args, **kwargs)
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.info(f"<<< {name.upper()} done in {elapsed:.0f}s ({len(members)} members)")
            scrape_status[name] = {
                "status": "success",
                "count": len(members),
                "timestamp": now_iso,
                "duration_s": round(elapsed, 1),
            }
            return members
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.error(f"<<< {name.upper()} FAILED after {elapsed:.0f}s: {e}")
            scrape_status[name] = {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "timestamp": now_iso,
                "duration_s": round(elapsed, 1),
            }
            return []

    source_results.append(run_source("cncef", scrape_cncef, max_pages=500, enrich_details=False))
    source_results.append(run_source("cncgp", scrape_cncgp))  # signature: (departments=None)
    source_results.append(run_source("anacofi", scrape_anacofi))
    source_results.append(run_source("affo", scrape_affo))

    # Merge all sources
    logger.info(">>> Merging sources...")
    merged_members = merge_all_sources(*source_results)

    # Enrich data.gouv (SIREN, dirigeants, creation_date) for any cabinet that's
    # missing one of those — covers both newly detected members AND old ones the
    # API failed on previously.
    logger.info(">>> Enriching data.gouv (SIREN/dirigeants/creation_date)...")
    dg_t0 = datetime.now(timezone.utc)
    try:
        batch_enrich_datagouv(merged_members)
        dg_status = "success"
        dg_err = None
    except Exception as e:
        dg_status = "error"
        dg_err = f"{type(e).__name__}: {e}"
        logger.error(f"data.gouv enrichment failed: {e}")
    dg_elapsed = (datetime.now(timezone.utc) - dg_t0).total_seconds()
    logger.info(f"<<< data.gouv enrichment done in {dg_elapsed:.0f}s")
    scrape_status["datagouv_enrichment"] = {
        "status": dg_status,
        "duration_s": round(dg_elapsed, 1),
        "timestamp": now_iso,
        **({"error": dg_err} if dg_err else {}),
    }

    # Enrich ORIAS detail (address, phone, email, orias_inscription_date) for
    # every cabinet with a SIREN that is missing one of those fields.
    logger.info(">>> Enriching ORIAS detail (address/phone/email)...")
    or_t0 = datetime.now(timezone.utc)
    try:
        batch_enrich_orias(merged_members)
        or_status = "success"
        or_err = None
    except Exception as e:
        or_status = "error"
        or_err = f"{type(e).__name__}: {e}"
        logger.error(f"ORIAS enrichment failed: {e}")
    or_elapsed = (datetime.now(timezone.utc) - or_t0).total_seconds()
    logger.info(f"<<< ORIAS enrichment done in {or_elapsed:.0f}s")
    scrape_status["orias_enrichment"] = {
        "status": or_status,
        "duration_s": round(or_elapsed, 1),
        "timestamp": now_iso,
        **({"error": or_err} if or_err else {}),
    }

    # Enrich emails from company websites (last — fills the gaps the official
    # registries didn't have)
    logger.info(">>> Enriching emails from websites...")
    enrich_t0 = datetime.now(timezone.utc)
    merged_members = batch_enrich_emails(merged_members, max_lookups=200)
    enrich_elapsed = (datetime.now(timezone.utc) - enrich_t0).total_seconds()
    logger.info(f"<<< Email enrichment done in {enrich_elapsed:.0f}s")
    scrape_status["email_enrichment"] = {
        "status": "success",
        "duration_s": round(enrich_elapsed, 1),
        "timestamp": now_iso,
    }

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
