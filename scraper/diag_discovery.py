"""Diagnostic: understand why discovered SIRENs look inconsistent.
Runs on GitHub Actions (network to data.gouv works there)."""
import json, requests, time
API = "https://recherche-entreprises.api.gouv.fr/search"
H = {"User-Agent": "cgp-monitor-diag/1.0"}

# SIRENs from removed 'discovered' cabinets
SAMPLE = {
    "SOLVA CONSEIL": "106815780",
    "SQUARE PARTNERS": "106767486",
    "LECHAT PATRIMOINE": "106143399",
    "BLACKWELL FAMILY OFFICE": "104453550",
}

print("=== A) Look up each suspect SIREN directly ===")
for name, siren in SAMPLE.items():
    try:
        r = requests.get(API, params={"q": siren, "per_page": 1}, headers=H, timeout=30)
        j = r.json()
        res = j.get("results", [])
        if not res:
            print(f"  {name} [{siren}]: NOT FOUND in registry (0 results)")
        else:
            e = res[0]
            print(f"  {name} [{siren}]: found -> siren={e.get('siren')} nom={e.get('nom_complet')!r} "
                  f"date_creation={e.get('date_creation')} naf={(e.get('siege') or {}).get('activite_principale')} "
                  f"etat={e.get('etat_administratif')}")
    except Exception as ex:
        print(f"  {name} [{siren}]: ERROR {ex}")
    time.sleep(0.3)

print()
print("=== B) Raw discovery query: NAF 66.19B created in last 30 days (DD-MM-YYYY) ===")
from datetime import datetime, timezone, timedelta
cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%d-%m-%Y")
print("date_creation_min =", cutoff)
try:
    r = requests.get(API, params={"activite_principale": "66.19B", "etat_administratif": "A",
                                   "date_creation_min": cutoff, "page": 1, "per_page": 8}, headers=H, timeout=30)
    j = r.json()
    print("total_results =", j.get("total_results"))
    for e in j.get("results", []):
        print(f"  siren={e.get('siren')} nom={e.get('nom_complet')!r} date_creation={e.get('date_creation')} "
              f"nj={e.get('nature_juridique')}")
except Exception as ex:
    print("ERROR:", ex)

print()
print("=== C) Sanity: a known-recent real CGP query, sorted, no date filter ===")
try:
    r = requests.get(API, params={"activite_principale": "66.19B", "etat_administratif": "A",
                                   "page": 1, "per_page": 5}, headers=H, timeout=30)
    j = r.json()
    print("total NAF 66.19B actives =", j.get("total_results"))
    for e in j.get("results", [])[:5]:
        print(f"  siren={e.get('siren')} nom={e.get('nom_complet')!r} date_creation={e.get('date_creation')}")
except Exception as ex:
    print("ERROR:", ex)
