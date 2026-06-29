"""
Daily data.gouv enrichment.

Fills `creation_date`, `dirigeants` and `siren` for members that are missing
them, by querying the public Recherche d'Entreprises API
(recherche-entreprises.api.gouv.fr). This is what powers the "Créés < 7 jours"
and "Créés < 4 mois" counters and the sort-by-creation-date in the UI.

The full member set is large, so each run is capped (MAX_LOOKUPS) and writes
checkpoints; consecutive daily runs converge to full coverage and then only
need to top up newly-added members.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))

from sources.recherche_entreprises import batch_enrich as batch_enrich_datagouv
from detector import build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")

# Cap per run so we stay well under the workflow timeout (~0.2s/lookup).
MAX_LOOKUPS = 3000


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
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    data = load_json(MEMBERS_PATH, {"members": [], "scrape_status": {}})
    members = data.get("members", [])
    logger.info(f"Loaded {len(members)} members")

    missing_cd = sum(1 for m in members if not m.get("creation_date"))
    missing_dir = sum(1 for m in members if not m.get("directors"))
    missing_siren = sum(1 for m in members if not m.get("siren"))
    logger.info(f"Before: missing creation_date={missing_cd}, "
                f"dirigeants={missing_dir}, siren={missing_siren}")

    def checkpoint():
        data["members"] = members
        data["last_updated"] = now_iso
        save_json(data, MEMBERS_PATH)
        logger.info("  checkpoint saved")

    batch_enrich_datagouv(members, max_lookups=MAX_LOOKUPS,
                          checkpoint_fn=checkpoint, checkpoint_every=500)

    after_cd = sum(1 for m in members if not m.get("creation_date"))
    after_dir = sum(1 for m in members if not m.get("directors"))
    after_siren = sum(1 for m in members if not m.get("siren"))
    logger.info(f"After: missing creation_date={after_cd}, "
                f"dirigeants={after_dir}, siren={after_siren}")
    logger.info(f"Filled this run: +{missing_cd - after_cd} dates, "
                f"+{missing_dir - after_dir} dirigeants, "
                f"+{missing_siren - after_siren} sirens")

    scrape_status = data.get("scrape_status", {})
    scrape_status["datagouv_enrich"] = {
        "status": "success",
        "timestamp": now_iso,
        "missing_creation_date": after_cd,
    }
    data["scrape_status"] = scrape_status

    stats = build_stats(members, 0, today)
    stats["scrape_status"] = scrape_status
    stats["last_updated"] = now_iso

    data["members"] = members
    data["last_updated"] = now_iso
    data["stats"] = stats
    save_json(data, MEMBERS_PATH)
    save_json(stats, STATS_PATH)
    logger.info("Done.")


if __name__ == "__main__":
    main()
