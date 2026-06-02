"""Probe: find the working date-creation filter param for recherche-entreprises."""
import logging, requests
from datetime import datetime, timezone, timedelta
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
API="https://recherche-entreprises.api.gouv.fr/search"
H={"User-Agent":"cgp-monitor/1.0"}
NAF="66.19B"
d7=datetime.now(timezone.utc)-timedelta(days=7)
iso=d7.strftime("%Y-%m-%d"); ddmm=d7.strftime("%d-%m-%Y")

def tot(params):
    try:
        r=requests.get(API,params=params,headers=H,timeout=30)
        if r.status_code!=200:
            return f"HTTP {r.status_code}: {r.text[:120]}"
        return r.json().get("total_results")
    except Exception as e:
        return f"ERR {e}"

base={"activite_principale":NAF,"etat_administratif":"A","per_page":1}
log.info(f"cutoff iso={iso} ddmm={ddmm}")
log.info(f"unfiltered total = {tot(base)}")
for name in ["date_creation_min","date_immatriculation_min","date_debut_activite_min"]:
    for val,fmt in [(iso,'iso'),(ddmm,'ddmm')]:
        log.info(f"{name}={val} ({fmt}) -> total = {tot({**base, name: val})}")
