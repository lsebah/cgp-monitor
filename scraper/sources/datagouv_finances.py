"""
data.gouv finances enricher.

Endpoint: https://recherche-entreprises.api.gouv.fr/search?q={SIREN}
Free, no key required, ~5 req/s tolerated.

Provides per cabinet:
- finances: latest year CA + résultat net (data.gouv exposes 1-3 years rolling)
- effectif: tranche INSEE (proxy de taille équipe)
- site_internet: parfois rempli (rare pour CGP, ~0%)
- naf_code: secteur

Stored on the member as:
    member["finances_data_gouv"] = {
        "year": 2023,
        "ca_eur": 327929,
        "resultat_net_eur": 1304,
        "effectif_tranche": "00",         # INSEE code
        "effectif_label": "0 salarié",
        "categorie_entreprise": "PME",
        "naf": "66.19A",
        "naf_label": "...",
        "last_enriched": "2026-05-07"
    }

Run via the orchestrator (scripts/enrich_websites.py --datagouv-finances).
"""
import logging
from datetime import datetime

from .base import fetch

logger = logging.getLogger(__name__)

API_URL = "https://recherche-entreprises.api.gouv.fr/search"

# INSEE tranche d'effectif salarié labels
EFFECTIF_LABELS = {
    "NN": "Non renseigné",
    "00": "0 salarié",
    "01": "1-2 salariés",
    "02": "3-5 salariés",
    "03": "6-9 salariés",
    "11": "10-19 salariés",
    "12": "20-49 salariés",
    "21": "50-99 salariés",
    "22": "100-199 salariés",
    "31": "200-249 salariés",
    "32": "250-499 salariés",
    "41": "500-999 salariés",
    "42": "1000-1999 salariés",
    "51": "2000-4999 salariés",
    "52": "5000-9999 salariés",
    "53": "10000+ salariés",
}


# Minimum financial year to keep — pre-2023 data is too stale for prospection
# (data.gouv often serves 2017-2019 figures for radiated/dormant SAS).
MIN_FINANCE_YEAR = 2023


def _latest_finances(finances_dict, min_year=MIN_FINANCE_YEAR):
    """data.gouv returns {"2019": {"ca": ..., "resultat_net": ...}, "2020": {...}}.
    Pick the most recent year >= min_year with at least CA or resultat_net."""
    if not finances_dict:
        return None, None
    try:
        years = sorted((int(y) for y in finances_dict.keys()), reverse=True)
    except (TypeError, ValueError):
        return None, None
    for y in years:
        if y < min_year:
            continue
        f = finances_dict.get(str(y)) or {}
        if f.get("ca") is not None or f.get("resultat_net") is not None:
            return y, f
    return None, None


def enrich_member(member):
    """Pull finances + effectif + site_internet from data.gouv. Mutates member.

    Returns True if any new data was added, False otherwise.
    """
    siren = (member.get("siren") or "").strip()
    if not siren or len(siren) != 9:
        return False

    # Skip if already enriched within last 30 days (idempotent re-runs)
    existing = member.get("finances_data_gouv") or {}
    if existing.get("last_enriched"):
        try:
            d = datetime.strptime(existing["last_enriched"], "%Y-%m-%d")
            if (datetime.utcnow() - d).days < 30:
                return False
        except ValueError:
            pass

    try:
        # Rate limit on the API kicks in around 7-10 req/s. We deliberately
        # stay below it: 0.4s = ~2.5 req/s.
        resp = fetch(API_URL, params={"q": siren, "page": 1, "per_page": 1},
                     delay=0.4, max_retries=3)
        payload = resp.json() if resp else None
    except Exception as e:
        logger.debug(f"data.gouv finances fetch failed for {siren}: {e}")
        return False

    if not payload:
        return False
    results = payload.get("results", [])
    if not results:
        return False
    e = results[0]
    if e.get("siren") != siren:
        return False

    fd = {"last_enriched": datetime.utcnow().strftime("%Y-%m-%d")}

    year, fin = _latest_finances(e.get("finances"))
    if year and fin:
        fd["year"] = year
        if fin.get("ca") is not None:
            fd["ca_eur"] = int(fin["ca"])
        if fin.get("resultat_net") is not None:
            fd["resultat_net_eur"] = int(fin["resultat_net"])

    eff = e.get("tranche_effectif_salarie")
    if eff:
        fd["effectif_tranche"] = eff
        fd["effectif_label"] = EFFECTIF_LABELS.get(eff, eff)
    if e.get("annee_tranche_effectif_salarie"):
        fd["effectif_year"] = e["annee_tranche_effectif_salarie"]

    if e.get("categorie_entreprise"):
        fd["categorie_entreprise"] = e["categorie_entreprise"]

    if e.get("activite_principale"):
        fd["naf"] = e["activite_principale"]

    # site_internet — rarement rempli mais on prend si présent et le membre n'en a pas
    site = e.get("site_internet")
    if site and not member.get("website"):
        # Light validation: must look like a URL
        if site.startswith(("http://", "https://", "www.")):
            member["website"] = site if site.startswith("http") else f"http://{site}"

    member["finances_data_gouv"] = fd
    return True


def batch_enrich(members, max_lookups=None, log_every=100, checkpoint_fn=None,
                 checkpoint_every=500):
    """Enrich members with data.gouv finances + effectif.

    `checkpoint_fn`, if provided, is called every `checkpoint_every` rows so
    the in-memory enrichment is persisted to disk in case of a later crash.
    """
    candidates = [m for m in members if m.get("siren")]
    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"data.gouv finances: {len(candidates)} candidates")

    found_ca = found_eff = found_site = 0
    skipped = 0
    for i, m in enumerate(candidates, 1):
        before_site = bool(m.get("website"))
        ok = enrich_member(m)
        if not ok:
            skipped += 1
            continue
        fd = m.get("finances_data_gouv") or {}
        if fd.get("ca_eur"):
            found_ca += 1
        if fd.get("effectif_tranche") and fd["effectif_tranche"] != "NN":
            found_eff += 1
        if not before_site and m.get("website"):
            found_site += 1

        if i % log_every == 0:
            logger.info(
                f"  data.gouv finances {i}/{len(candidates)} "
                f"(+{found_ca} CA, +{found_eff} effectifs, +{found_site} sites)"
            )

        if checkpoint_fn and (i % checkpoint_every == 0):
            try:
                checkpoint_fn()
                logger.info(f"  -> checkpoint saved at {i}/{len(candidates)}")
            except Exception as ex:
                logger.warning(f"  checkpoint failed: {ex}")

    logger.info(
        f"data.gouv finances done: +{found_ca} CA, +{found_eff} effectifs, "
        f"+{found_site} sites (out of {len(candidates)} lookups, {skipped} skipped)"
    )
    return members
