"""
Enrichissement par site web cabinet — 3 phases :

  Phase A (FAST) — data.gouv finances : CA + résultat net + effectif
                   ~5 req/s, ~30 min pour 8000 cabinets avec SIREN.
  Phase B (SLOW) — website_content : encours, expertises, structurés, équipe
                   ~5 s/cabinet, dépend du nb de sites connus.
  Phase C (TRES SLOW) — website_finder : découverte de site via DDG/Bing
                       ~5 s/cabinet, ~10000 cabinets sans site.

Usage :
  python scripts/enrich_websites.py                       # tout (A puis B)
  python scripts/enrich_websites.py --datagouv-only       # phase A
  python scripts/enrich_websites.py --content-only        # phase B
  python scripts/enrich_websites.py --discover-only       # phase C
  python scripts/enrich_websites.py --limit 50            # test sur 50
  python scripts/enrich_websites.py --filter cncgp        # filtrer par asso
  python scripts/enrich_websites.py --content-only --limit 10  # test rapide

Le script checkpoint régulièrement dans docs/data/members.json — Ctrl+C
n'est pas catastrophique, on reprend où on en était au prochain run (les
cabinets déjà enrichis sont skippés).
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

from sources.datagouv_finances import batch_enrich as enrich_datagouv_finances
from sources.website_content import batch_enrich as enrich_website_content
from sources.website_finder import batch_find as discover_websites

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


def filter_members(members, assoc_filter=None):
    if not assoc_filter:
        return members
    codes = [c.strip().lower() for c in assoc_filter.split(",")]
    return [
        m for m in members
        if any((m.get("associations") or {}).get(c, {}).get("member") for c in codes)
    ]


def report_coverage(members, label):
    n = len(members)
    if not n:
        return
    site = sum(1 for m in members if m.get("website"))
    fin = sum(1 for m in members if (m.get("finances_data_gouv") or {}).get("ca_eur"))
    aum = sum(1 for m in members if (m.get("website_data") or {}).get("aum_eur"))
    sp = sum(1 for m in members if (m.get("website_data") or {}).get("has_structured_products"))
    team = sum(1 for m in members if (m.get("website_data") or {}).get("team_url"))
    eff = sum(1 for m in members
              if (m.get("finances_data_gouv") or {}).get("effectif_tranche")
              and (m.get("finances_data_gouv") or {}).get("effectif_tranche") != "NN")
    logger.info(
        f"[{label}] n={n} | site={site} ({100*site//n}%) | CA={fin} ({100*fin//n}%) "
        f"| effectif={eff} ({100*eff//n}%) | AUM={aum} | structures={sp} | team={team}"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--datagouv-only", action="store_true",
                   help="Phase A seulement (CA + effectif via data.gouv)")
    p.add_argument("--content-only", action="store_true",
                   help="Phase B seulement (scrape contenu sites connus)")
    p.add_argument("--discover-only", action="store_true",
                   help="Phase C seulement (découverte de sites via search)")
    p.add_argument("--all-phases", action="store_true",
                   help="A + B + C (long, peut prendre 24h)")
    p.add_argument("--limit", type=int, help="Limiter au N premiers candidats")
    p.add_argument("--filter", help="Filtrer par asso(s) ex: cncgp,cncef")
    p.add_argument("--no-browser-fallback", action="store_true",
                   help="Phase B: ne pas tenter Playwright si requests échoue")
    p.add_argument("--rebuild", action="store_true",
                   help="Phase B/A: re-scrape même si website_data déjà rempli")
    args = p.parse_args()

    data = load_members()
    members = data.get("members") if isinstance(data, dict) and "members" in data else data
    target = filter_members(members, args.filter)

    report_coverage(target, "AVANT")

    def checkpoint():
        save_members(data)

    # Default behavior: A + B (skip C unless explicit)
    do_a = args.datagouv_only or args.all_phases or (
        not args.content_only and not args.discover_only
    )
    do_b = args.content_only or args.all_phases or (
        not args.datagouv_only and not args.discover_only
    )
    do_c = args.discover_only or args.all_phases

    if args.rebuild:
        # Wipe website_data + finances_data_gouv on the target members so they
        # get re-fetched on this run.
        for m in target:
            m.pop("website_data", None)
            m.pop("finances_data_gouv", None)

    if do_a:
        logger.info(">>> Phase A : data.gouv finances (CA + effectif)")
        enrich_datagouv_finances(target, max_lookups=args.limit,
                                 checkpoint_fn=checkpoint, checkpoint_every=500)
        save_members(data)
        report_coverage(target, "APRES A")

    if do_b:
        logger.info(">>> Phase B : scrape contenu sites connus")
        enrich_website_content(target, max_lookups=args.limit,
                               checkpoint_fn=checkpoint, checkpoint_every=50)
        save_members(data)
        report_coverage(target, "APRES B")

    if do_c:
        logger.info(">>> Phase C : découverte de sites via search engine")
        discover_websites(target, max_lookups=args.limit,
                          checkpoint_fn=checkpoint, checkpoint_every=100)
        save_members(data)
        report_coverage(target, "APRES C")

    save_members(data)
    logger.info("Done.")


if __name__ == "__main__":
    main()
