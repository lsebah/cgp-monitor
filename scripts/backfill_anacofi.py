"""
One-shot backfill: enrich ANACOFI members (3243 entries) with adresse, phone,
email (ORIAS), dirigeants (data.gouv) and LinkedIn URLs (DDG).

Run from repo root:
    python scripts/backfill_anacofi.py            # full run, all 3 passes
    python scripts/backfill_anacofi.py --no-linkedin  # skip the slow DDG pass
    python scripts/backfill_anacofi.py --limit 100    # test on a subset

The script writes back into docs/data/members.json with periodic checkpoints,
so you can interrupt with Ctrl+C and resume — already-enriched members are
skipped on the next run.
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRAPER = os.path.join(REPO, "scraper")
sys.path.insert(0, SCRAPER)

from sources.orias_detail import batch_enrich as enrich_orias
from sources.recherche_entreprises import batch_enrich as enrich_datagouv
from sources.linkedin_search import batch_enrich as enrich_linkedin

DATA_DIR = os.path.join(REPO, "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_members():
    with open(MEMBERS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_members(data):
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(MEMBERS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def filter_anacofi(members):
    return [m for m in members if (m.get("associations") or {}).get("anacofi", {}).get("member")]


def report_coverage(anacofi, label):
    n = len(anacofi)
    if not n:
        return
    addr = sum(1 for m in anacofi if (m.get("address") or {}).get("street"))
    phone = sum(1 for m in anacofi if m.get("phone"))
    email = sum(1 for m in anacofi if m.get("email"))
    directors = sum(1 for m in anacofi if m.get("directors"))
    li = sum(
        1
        for m in anacofi
        for d in (m.get("directors") or [])
        if d.get("linkedin")
    )
    logger.info(
        f"[{label}] coverage: addr={addr}/{n} ({100*addr//n}%) | "
        f"phone={phone}/{n} ({100*phone//n}%) | "
        f"email={email}/{n} ({100*email//n}%) | "
        f"dirigeants={directors}/{n} ({100*directors//n}%) | "
        f"linkedin={li}"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Limit each pass to N members (testing)")
    p.add_argument("--linkedin-limit", type=int, default=None,
                   help="Separate cap for the slow LinkedIn pass "
                        "(e.g. 300 ≈ 1h, 0 to skip). Defaults to --limit if set.")
    p.add_argument("--no-orias", action="store_true")
    p.add_argument("--no-datagouv", action="store_true")
    p.add_argument("--no-linkedin", action="store_true",
                   help="Skip the slow DDG LinkedIn pass")
    p.add_argument("--linkedin-delay", type=float, default=2.5,
                   help="Delay between DDG searches (seconds)")
    args = p.parse_args()

    data = load_members()
    members = data["members"]
    anacofi = filter_anacofi(members)
    logger.info(f"Loaded {len(members)} total members, {len(anacofi)} ANACOFI")
    report_coverage(anacofi, "BEFORE")

    start = time.time()

    # PASS 1 — ORIAS detail pages: address + phone + email
    if not args.no_orias:
        logger.info("=" * 60)
        logger.info("PASS 1 — ORIAS detail pages (address + phone + email)")
        logger.info("=" * 60)
        t0 = time.time()
        enrich_orias(anacofi, max_lookups=args.limit)
        logger.info(f"Pass 1 done in {time.time()-t0:.0f}s")
        save_members(data)
        report_coverage(anacofi, "AFTER ORIAS")

    # PASS 2 — data.gouv API: dirigeants
    if not args.no_datagouv:
        logger.info("=" * 60)
        logger.info("PASS 2 — data.gouv API (dirigeants)")
        logger.info("=" * 60)
        t0 = time.time()
        enrich_datagouv(anacofi, max_lookups=args.limit)
        logger.info(f"Pass 2 done in {time.time()-t0:.0f}s")
        save_members(data)
        report_coverage(anacofi, "AFTER data.gouv")

    # PASS 3 — DDG LinkedIn search (slow; can be skipped or capped tighter)
    li_limit = args.linkedin_limit if args.linkedin_limit is not None else args.limit
    if not args.no_linkedin and li_limit != 0:
        logger.info("=" * 60)
        logger.info(f"PASS 3 — DDG LinkedIn search (URLs for dirigeants, cap={li_limit})")
        logger.info("=" * 60)
        t0 = time.time()
        enrich_linkedin(anacofi, max_lookups=li_limit, delay=args.linkedin_delay)
        logger.info(f"Pass 3 done in {time.time()-t0:.0f}s")
        save_members(data)
        report_coverage(anacofi, "AFTER LinkedIn")

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info(f"BACKFILL DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    logger.info("=" * 60)
    report_coverage(anacofi, "FINAL")


if __name__ == "__main__":
    main()
