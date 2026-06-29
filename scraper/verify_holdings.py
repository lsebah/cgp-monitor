"""
One-off: verify the ambiguous holding/participation members against data.gouv
to objectively classify them by their real NAF code.

KEEP  if NAF is a financial-advisory code (66.xx) — a real CGP / courtage firm.
DROP  if NAF is a pure-holding / real-estate code (64.20Z, 64.2x, 70.10Z, 68.xx)
       — a holding SPV or real-estate entity, not a CGP cabinet.
KEEP  if the firm can't be resolved (stay safe — it's association-verified).
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(__file__))

import requests
from detector import build_stats

MEMBERS_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "members.json")
STATS_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "stats.json")
API = "https://recherche-entreprises.api.gouv.fr/search"
HDRS = {"User-Agent": "cgp-monitor/1.0"}

SUSPECTS = {
    "ALTIMEO PARTICIPATIONS", "BAC HOLDING", "BALMAT HOLDING", "CAB HOLDING",
    "DWBH PARTICIPATION", "HOLDING GROUPE LEBLOND SAMPAIO", "HOLDING MINITAUX",
    "HOLDING TE MAHANA", "INSTITUT LORRAIN DE PARTICIPATION", "J10 PARTICIPATION",
    "LAG PARTICIPATIONS", "LAGUNE HOLDING", "L'IMMOBILIERE INVEST",
    "PRISCUS PARTICIPATIONS", "SIGNATURE PARTICIPATIONS", "TATEY HOLDING",
}


def norm(s):
    return (s or "").strip().upper().replace("’", "'")


def api_get(params):
    for attempt in range(4):
        try:
            r = requests.get(API, params=params, headers=HDRS, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt); continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            time.sleep(1 + attempt)
    return None


def lookup_naf(m):
    """Return (naf, nom) from data.gouv, by SIREN then by name+postal."""
    siren = (m.get("siren") or "").strip()
    if siren and len(siren) == 9:
        j = api_get({"q": siren, "page": 1, "per_page": 1})
        if j and j.get("results"):
            e = j["results"][0]
            if e.get("siren") == siren:
                return (e.get("siege") or {}).get("activite_principale") or e.get("activite_principale"), e.get("nom_complet")
    name = m.get("company_name", "")
    cp = (m.get("address") or {}).get("postal_code") or ""
    params = {"q": name, "page": 1, "per_page": 1}
    if cp:
        params["code_postal"] = cp
    j = api_get(params)
    if j and j.get("results"):
        e = j["results"][0]
        return (e.get("siege") or {}).get("activite_principale") or e.get("activite_principale"), e.get("nom_complet")
    return None, None


def main():
    d = json.load(open(MEMBERS_PATH, encoding="utf-8"))
    ms = d["members"]

    # data.gouv NAF prefixes that ARE financial advisory / CGP -> keep
    KEEP_PREFIX = ("66.",)
    # explicit non-CGP -> drop (holdings, real estate, mgmt holdings)
    DROP_EXACT = {"64.20Z", "64.2", "70.10Z", "70.22Z"}
    def is_drop(naf):
        if not naf:
            return False
        if naf.startswith("66."):
            return False
        if naf.startswith(("64.2", "70.10", "68.")):
            return True
        return False  # unknown/other -> keep (stay safe)

    verdicts = []
    for m in ms:
        if norm(m.get("company_name", "")) in SUSPECTS:
            naf, nom = lookup_naf(m)
            drop = is_drop(naf)
            verdicts.append((m, m.get("company_name", ""), naf, drop))
            log.info(f"{'DROP' if drop else 'KEEP'} | {m.get('company_name','')[:40]:40} "
                     f"| NAF={naf} | {nom or ''}")
            time.sleep(0.3)

    to_drop = {id(v[0]) for v in verdicts if v[3]}
    if not to_drop:
        log.info("No holding to drop after NAF check. Nothing changed.")
        return

    kept = [m for m in ms if id(m) not in to_drop]
    log.info(f"Dropping {len(to_drop)} non-CGP holdings; {len(ms)} -> {len(kept)}")

    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d["members"] = kept
    stats = build_stats(kept, 0, today)
    stats["scrape_status"] = d.get("scrape_status", {})
    stats["last_updated"] = now
    d["stats"] = stats
    d["last_updated"] = now
    json.dump(d, open(MEMBERS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    json.dump(stats, open(STATS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    log.info(f"Saved. Total: {stats['total_members']}")


if __name__ == "__main__":
    main()
