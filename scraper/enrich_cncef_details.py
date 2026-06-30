"""
#5 — Improve CNCEF members' address from their detail page.

CNCEF list pages only give name + department label (e.g. city="Essonne",
postal synthesised) — too weak for a confident data.gouv SIREN match. Each
CNCEF detail page exposes the real "Siege social" (street + postal + city) and
the legal representative. Filling the real address lets the (validated)
data.gouv matcher resolve the SIREN safely on the next enrichment pass — we do
NOT force any SIREN here, so no risk of mis-attribution.

Bounded per run (the CNCEF server throttles), sequential over keep-alive,
runs daily; consecutive runs converge.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(__file__))

import requests
from bs4 import BeautifulSoup
from sources.cncef import _parse_detail_page
from detector import build_stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")

MAX_LOOKUPS = 400   # detail pages per run (server throttles; daily runs converge)


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

    # CNCEF members without a SIREN that still have only a department-level
    # address (city looks like a department or postal ends in '000').
    def needs_fix(m):
        if "cncef" not in (m.get("associations") or {}):
            return False
        if m.get("siren"):
            return False
        if not (m.get("source_urls") or {}).get("cncef"):
            return False
        if m.get("address_detail_done"):
            return False
        return True

    candidates = [m for m in members if needs_fix(m)][:MAX_LOOKUPS]
    logger.info(f"CNCEF address enrichment: {len(candidates)} candidates this run")

    fixed = 0
    for i, m in enumerate(candidates, 1):
        url = m["source_urls"]["cncef"]
        try:
            info = _parse_detail_page(url)
        except Exception as e:
            logger.debug(f"detail parse failed {url}: {e}")
            info = {}
        if info.get("city") or info.get("postal_code") or info.get("address_street"):
            addr = m.setdefault("address", {})
            if info.get("address_street"):
                addr["street"] = info["address_street"]
            if info.get("postal_code"):
                addr["postal_code"] = info["postal_code"]
            if info.get("city"):
                addr["city"] = info["city"]
            fixed += 1
        # capture legal rep if we have none
        if info.get("director_name") and not m.get("directors"):
            m["directors"] = [{"name": info["director_name"],
                               "role": info.get("director_role") or "Dirigeant",
                               "source": "cncef"}]
        m["address_detail_done"] = True   # don't refetch next run
        if i % 50 == 0:
            logger.info(f"  {i}/{len(candidates)} (+{fixed} addresses)")
            data["members"] = members
            save_json(data, MEMBERS_PATH)
        time.sleep(0.3)

    logger.info(f"CNCEF address enrichment done: +{fixed} real addresses")

    ss = data.get("scrape_status", {})
    ss["cncef_detail_enrich"] = {"status": "success", "timestamp": now_iso,
                                 "fixed": fixed}
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


if __name__ == "__main__":
    main()
