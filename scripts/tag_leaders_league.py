"""
Tag every cabinet in members.json with a `groupement` value when its name
matches one of the Leaders League 2025 groups.

Source: https://www.leadersleague.com/fr/classements/gestion-de-patrimoine-...

Most of these groupements DO NOT publish a public list of their member
cabinets, so we can't scrape them like we did for Magnacarta / Laplace /
Cyrus. Instead we infer the affiliation when the cabinet name itself
contains the group's distinctive token ("Patrimmofi", "Astoria",
"Metagram", ...). False positives are rare because these are branded
trade-names, not generic CGP words.

Run:
    python scripts/tag_leaders_league.py             # dry-run + report
    python scripts/tag_leaders_league.py --apply     # write back to members.json
"""
import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO, "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# Each entry: canonical groupement name → (regex pattern, tier)
# Patterns are conservative: we anchor on word boundaries and prefer
# multi-word matches when the bare token would be ambiguous.
LEADERS_LEAGUE_2025 = {
    # Tier "Incontournable"
    "Astoria Finance":   (r"\bastoria\b",                                       "Incontournable"),
    "Groupe Premium":    (r"\bgroupe\s+premium\b|\bpremium\s+(conseil|patrimoine|finance)\b", "Incontournable"),
    # Tier "Excellent"
    "Aliquis Conseil":   (r"\baliquis\b",                                       "Excellent"),
    "Groupe Orion":      (r"\borion\b",                                          "Excellent"),
    "Rhétorès Groupe":   (r"\brh[ée]tor[èe]s\b",                                 "Excellent"),
    # Tier "Forte notoriété"
    "Finzzle Groupe":    (r"\bfinzzle\b",                                        "Forte notoriété"),
    "Groupe CIEC":       (r"\bgroupe\s+ciec\b|\bCIEC\s+(conseil|patrimoine|finance)\b", "Forte notoriété"),
    "Patrimmofi":        (r"\bpatrimmofi\b",                                     "Forte notoriété"),
    "Olifan Group":      (r"\bolifan\b",                                         "Forte notoriété"),
    # Tier "Pratique réputée"
    "Advenis Gestion Privée": (r"\badvenis\b",                                  "Pratique réputée"),
    "Equance":           (r"\bequance\b",                                        "Pratique réputée"),
    "Metagram":          (r"\bmetagram\b",                                       "Pratique réputée"),
    "UFF":               (r"\bUFF\b|\bunion\s+financi[èe]re\s+de\s+france\b",   "Pratique réputée"),
    "Valoria":           (r"\bvaloria\b",                                        "Pratique réputée"),
    # Tier "Pratique de qualité"
    "Groupe Rayne":      (r"\bgroupe\s+rayne\b",                                 "Pratique de qualité"),
    "Groupe Synalp":     (r"\bsynalp\b",                                         "Pratique de qualité"),
    "Pluralle":          (r"\bpluralle\b",                                       "Pratique de qualité"),
    "Vitalépargne":      (r"\bvital[ée]pargne\b",                                "Pratique de qualité"),
    # User-supplied (not in Leaders League listing but mentioned)
    "Les Associés":      (r"\bles\s+associ[ée]s\b",                              "User-listed"),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Write changes back to members.json (default = dry-run).")
    args = p.parse_args()

    with open(MEMBERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    members = data["members"]

    # Pre-compile patterns once
    patterns = [
        (groupement, re.compile(pat, re.IGNORECASE), tier)
        for groupement, (pat, tier) in LEADERS_LEAGUE_2025.items()
    ]

    matches_per_grp = {g: [] for g, _, _ in patterns}
    overrides = []  # cabinets that already had a `groupement` we won't override

    for m in members:
        name = m.get("company_name") or ""
        for groupement, rx, tier in patterns:
            if rx.search(name):
                # If a more-specific groupement is already set (UCGP scrape
                # established a true Magnacarta/Laplace tag), keep it.
                cur = m.get("groupement")
                if cur and cur != groupement:
                    overrides.append((m["id"], name, cur, groupement))
                else:
                    matches_per_grp[groupement].append(m)
                    if args.apply:
                        m["groupement"] = groupement
                        # Add the tier as a hint
                        m.setdefault("groupement_tier", tier)
                        # Also tag a virtual association (so the dropdown
                        # filter "Tous les groupements" picks it up).
                        m.setdefault("associations", {})[
                            "leaders_league"
                        ] = {"member": True, "groupement": groupement, "tier": tier}
                break  # one cabinet, one groupement

    print(f"\n=== Matches per groupement ===")
    total = 0
    for g, ms in matches_per_grp.items():
        if not ms:
            continue
        total += len(ms)
        sample = ", ".join(m["company_name"][:30] for m in ms[:3])
        print(f"  {g:30s}  {len(ms):4d}  → {sample}")
    print(f"\n  TOTAL: {total} cabinets matched")

    if overrides:
        print(f"\n=== {len(overrides)} cabinets kept their existing groupement (not overridden) ===")
        for _, name, cur, would_be in overrides[:10]:
            print(f"  '{name[:40]}' kept '{cur}' (would have been '{would_be}')")

    if args.apply:
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(MEMBERS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Wrote {MEMBERS_PATH} ({total} cabinets tagged)")
    else:
        logger.info("Dry-run only. Pass --apply to actually write back.")


if __name__ == "__main__":
    main()
