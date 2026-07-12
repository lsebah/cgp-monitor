"""
Boucle de contrôle : vérifie que le refresh hebdomadaire a bien eu lieu et
que les données sont saines. Écrit docs/data/health.json (affiché par le
site) et sort en code 1 si un problème bloquant est détecté (le workflow
ouvre alors une issue GitHub).

Contrôles :
  1. Fraîcheur   : last_updated < 8 jours (refresh hebdo + marge)
  2. Volume      : total membres dans une fourchette plausible
  3. Sources     : chaque association au-dessus de son plancher historique
  4. Statuts     : aucun scrape en 'error' au dernier passage
  5. Intégrité   : stats.json cohérent avec members.json, IDs uniques
"""
import json
import os
import sys
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
HEALTH_PATH = os.path.join(DATA_DIR, "health.json")

# Planchers par source (≈60 % du niveau connu : même logique que la garde CNCEF)
FLOORS = {"cncgp": 1600, "cncef": 2800, "anacofi": 1900}
TOTAL_RANGE = (8000, 15000)
MAX_AGE_DAYS = 8

_ORDER = {"ok": 0, "warn": 1, "error": 2}


def main():
    now = datetime.now(timezone.utc)
    checks = []
    state = {"status": "ok"}

    def check(name, ok, detail, blocking=True):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        if not ok:
            new = "error" if blocking else "warn"
            if _ORDER[new] > _ORDER[state["status"]]:
                state["status"] = new

    try:
        d = json.load(open(MEMBERS_PATH, encoding="utf-8"))
        s = json.load(open(STATS_PATH, encoding="utf-8"))
    except Exception as e:
        payload = {"status": "error", "checked_at": now.isoformat(),
                   "checks": [{"name": "load", "ok": False, "detail": f"JSON illisible: {e}"}]}
        json.dump(payload, open(HEALTH_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print("ERROR: cannot load data files:", e)
        sys.exit(1)

    members = d.get("members", [])

    # 1. Fraîcheur
    lu = d.get("last_updated", "")
    try:
        age_days = (now - datetime.fromisoformat(lu)).total_seconds() / 86400
    except Exception:
        age_days = 9999
    check("freshness", age_days <= MAX_AGE_DAYS,
          f"last_updated={lu[:19]} (il y a {age_days:.1f} j, max {MAX_AGE_DAYS})")

    # 2. Volume total
    n = len(members)
    check("total_members", TOTAL_RANGE[0] <= n <= TOTAL_RANGE[1],
          f"{n} membres (attendu {TOTAL_RANGE[0]}-{TOTAL_RANGE[1]})")

    # 3. Planchers par association
    counts = {}
    for m in members:
        for a in (m.get("associations") or {}):
            counts[a] = counts.get(a, 0) + 1
    for assoc, floor in FLOORS.items():
        c = counts.get(assoc, 0)
        check(f"floor_{assoc}", c >= floor, f"{assoc}={c} (plancher {floor})")

    # 4. Statuts de scrape (error/partial = avertissement : les données
    #    précédentes restent en place grâce aux gardes, mais il faut le voir)
    for src, st in (d.get("scrape_status") or {}).items():
        st_val = (st or {}).get("status", "?")
        if st_val == "error":
            check(f"scrape_{src}", False,
                  f"status=error ({(st or {}).get('error', '')[:80]})", blocking=False)
        elif st_val == "partial":
            check(f"scrape_{src}", False,
                  f"status=partial (count={st.get('count')}, kept={st.get('kept')})",
                  blocking=False)

    # 5. Intégrité
    check("stats_coherence", s.get("total_members") == n,
          f"stats={s.get('total_members')} vs members={n}")
    ids = [m.get("id") for m in members]
    check("unique_ids", len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} doublons d'ID")

    payload = {"status": state["status"], "checked_at": now.isoformat(), "checks": checks}
    json.dump(payload, open(HEALTH_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    for c in checks:
        print(("OK   " if c["ok"] else "FAIL ") + c["name"] + ": " + c["detail"])
    print("=> status:", state["status"])
    sys.exit(1 if state["status"] == "error" else 0)


if __name__ == "__main__":
    main()
