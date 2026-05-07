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

from sources.cncef import scrape_cncef, _parse_detail_page
from sources.base import clean_phone, clean_email, extract_department

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
    cur_addr = existing.get("address") or {}
    # If detail-enriched scrape brought a real street/postal, prefer it
    if fresh_addr.get("street") and fresh_addr.get("postal_code"):
        existing["address"] = fresh_addr
    elif fresh_addr.get("postal_code") and not cur_addr.get("postal_code"):
        # No street but a real postal code — fold it in
        cur_addr["postal_code"] = fresh_addr["postal_code"]
        for k in ("department", "department_name", "region"):
            if not cur_addr.get(k) and fresh_addr.get(k):
                cur_addr[k] = fresh_addr[k]
        existing["address"] = cur_addr
    elif fresh_addr.get("city") and not cur_addr.get("city"):
        for k in ("city", "department", "department_name", "region"):
            if fresh_addr.get(k):
                cur_addr[k] = fresh_addr[k]
        existing["address"] = cur_addr

    # Phone / email / website come from enrich_details — only fill blanks
    for key in ("phone", "email", "website"):
        if fresh.get(key) and not existing.get(key):
            existing[key] = fresh[key]
    # Directors: only fill if existing is empty
    if fresh.get("directors") and not existing.get("directors"):
        existing["directors"] = fresh["directors"]

    fresh_acts = set(fresh.get("activities") or [])
    cur_acts = set(existing.get("activities") or [])
    if fresh_acts:
        existing["activities"] = sorted(cur_acts | fresh_acts)
    existing.setdefault("associations", {})["cncef"] = {"member": True}
    if fresh.get("source_urls"):
        existing.setdefault("source_urls", {}).update(fresh["source_urls"])
    existing["last_seen"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _save(data, path=MEMBERS_PATH):
    """Stable save of the whole members.json."""
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as fout:
        json.dump(data, fout, indent=2, ensure_ascii=False)


def _enrich_detail_pass(cncef_members, ckpt_fn, checkpoint_every=250):
    """For each CNCEF member without a real postal_code, fetch the detail page
    and fill street/postal/city/director.

    "Real postal_code" excludes the synthetic "XX000" placeholders we generate
    when only the department code is known — those are useless for the
    data.gouv SIREN lookup and need to be replaced with the actual CP.
    """
    def needs_detail(m):
        if not (m.get("source_urls") or {}).get("cncef"):
            return False
        cp = (m.get("address") or {}).get("postal_code") or ""
        # No CP, or synthetic "XX000" placeholder
        return (not cp) or cp.endswith("000")

    candidates = [m for m in cncef_members if needs_detail(m)]
    logger.info(f"Detail enrichment: {len(candidates)} CNCEF members to enrich")
    found_pc = found_phone = found_email = found_web = 0
    from config import DEPARTMENTS, DEPT_TO_REGION

    for i, m in enumerate(candidates, 1):
        url = m["source_urls"]["cncef"]
        try:
            extra = _parse_detail_page(url)
        except Exception as e:
            logger.debug(f"  detail failed for {m.get('company_name')}: {e}")
            continue

        if not extra:
            continue

        cur_addr = m.get("address") or {}
        if extra.get("postal_code"):
            cur_addr["postal_code"] = extra["postal_code"]
            dept = extract_department(extra["postal_code"])
            if dept:
                cur_addr["department"] = dept
                cur_addr["department_name"] = DEPARTMENTS.get(dept, "")
                cur_addr["region"] = DEPT_TO_REGION.get(dept, "")
            found_pc += 1
        if extra.get("address_street") and not cur_addr.get("street"):
            cur_addr["street"] = extra["address_street"]
        if extra.get("city") and not cur_addr.get("city"):
            cur_addr["city"] = extra["city"]
        m["address"] = cur_addr

        # CNCEF detail pages don't expose cabinet phone/email/website — only
        # address + director. Phone/email will come later via ORIAS once the
        # SIREN is found by data.gouv (using the freshly populated CP).
        if extra.get("director_name") and not m.get("directors"):
            m["directors"] = [{
                "name": extra["director_name"],
                "role": extra.get("director_role") or "Représentant légal",
            }]

        if i % 50 == 0:
            logger.info(
                f"  detail {i}/{len(candidates)} (+{found_pc} CP, +{found_phone} phone, "
                f"+{found_email} email, +{found_web} web)"
            )
        if i % checkpoint_every == 0:
            try:
                ckpt_fn()
                logger.info(f"  -> checkpoint saved at {i}/{len(candidates)}")
            except Exception as e:
                logger.warning(f"  checkpoint failed: {e}")

    logger.info(
        f"Detail enrichment done: +{found_pc} CP, +{found_phone} phones, "
        f"+{found_email} emails, +{found_web} websites"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-pages", type=int, default=500)
    p.add_argument("--enrich-details", action="store_true",
                   help="Follow each cabinet's detail page to pull postal_code, "
                        "phone, email, website, director (~2-3h for the full base).")
    p.add_argument("--skip-list-scrape", action="store_true",
                   help="Skip the list re-scrape, only run --enrich-details on existing CNCEF rows.")
    args = p.parse_args()

    with open(MEMBERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    existing = data["members"]
    by_id = {m["id"]: m for m in existing}
    cncef_before = sum(
        1 for m in existing if (m.get("associations") or {}).get("cncef", {}).get("member")
    )
    logger.info(f"Loaded {len(existing)} members, {cncef_before} already tagged CNCEF")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not args.skip_list_scrape:
        fresh = scrape_cncef(max_pages=args.max_pages, enrich_details=False)
        logger.info(f"CNCEF list scrape returned {len(fresh)} cabinets")

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
            f"List pass: {updated} CNCEF rows updated, {added} new cabinets added. "
            f"Total CNCEF: {cncef_before} -> {cncef_after}"
        )
        # Save before the slow detail pass
        existing.sort(key=lambda m: (m.get("company_name") or "").lower())
        data["members"] = existing
        _save(data)

    if args.enrich_details:
        cncef_members = [
            m for m in data["members"]
            if (m.get("associations") or {}).get("cncef", {}).get("member")
        ]
        _enrich_detail_pass(cncef_members, ckpt_fn=lambda: _save(data))

    # Final stats refresh + save
    cncef_after = sum(
        1 for m in data["members"] if (m.get("associations") or {}).get("cncef", {}).get("member")
    )
    stats = data.setdefault("stats", {})
    stats.setdefault("by_association", {})["cncef"] = cncef_after
    stats["total_members"] = len(data["members"])
    data["members"].sort(key=lambda m: (m.get("company_name") or "").lower())
    _save(data)
    logger.info(f"DONE. Total CNCEF cabinets: {cncef_after}. Wrote {MEMBERS_PATH}")


if __name__ == "__main__":
    main()
