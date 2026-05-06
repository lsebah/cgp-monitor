"""
data.gouv enricher (API Recherche d'Entreprises).

Endpoint: https://recherche-entreprises.api.gouv.fr/search?q={SIREN}
Free, no key required, ~5-10 req/s tolerated.

Provides: dirigeants (the field ORIAS does not expose), siege address as a
fallback if ORIAS fiche missed it, NAF code, état admin (active/closed).

Director shape stored on the member:
    member["directors"] = [
        {"name": "GREGORY DANJOU", "role": "Gérant", "source": "data.gouv"}
    ]
"""
import logging

from .base import fetch

logger = logging.getLogger(__name__)

API_URL = "https://recherche-entreprises.api.gouv.fr/search"


def _format_director(d):
    """Turn API dirigeant record into a normalized {name, role, source} dict.

    The API returns either physical persons (nom + prénoms + qualité) or
    moral persons (siren_dirigeant + denomination). We keep both.
    """
    if d.get("type_dirigeant") == "personne morale":
        name = d.get("denomination") or d.get("sigle") or ""
        if not name:
            return None
        return {
            "name": name.strip(),
            "role": d.get("qualite") or "",
            "source": "data.gouv",
            "type": "personne morale",
        }
    # Physical person
    nom = (d.get("nom") or "").strip()
    prenoms = (d.get("prenoms") or "").strip()
    if not nom and not prenoms:
        return None
    # Normalize: "DANJOU GREGORY PIERRICK ROGER" → first prenom + nom
    first_prenom = prenoms.split()[0] if prenoms else ""
    full = f"{first_prenom} {nom}".strip()
    return {
        "name": full,
        "role": d.get("qualite") or "",
        "source": "data.gouv",
        "type": "personne physique",
    }


def enrich_member(member):
    """Fetch dirigeants + admin state from data.gouv API by SIREN.

    Mutates and returns the member dict.
    """
    siren = (member.get("siren") or "").strip()
    if not siren or len(siren) != 9:
        return member

    # Skip if directors already filled
    if member.get("directors"):
        return member

    try:
        resp = fetch(
            API_URL,
            params={"q": siren, "page": 1, "per_page": 1},
            delay=0.2,
            max_retries=2,
        )
        if not resp:
            return member
        payload = resp.json()
    except Exception as e:
        logger.debug(f"data.gouv fetch failed for {siren}: {e}")
        return member

    results = payload.get("results", [])
    if not results:
        return member
    entry = results[0]

    # Sanity check: SIREN must match (the API can return broader matches)
    if entry.get("siren") != siren:
        return member

    # Directors
    raw_dirigeants = entry.get("dirigeants") or []
    formatted = [d for d in (_format_director(rd) for rd in raw_dirigeants) if d]
    if formatted:
        member["directors"] = formatted

    # Admin state (informative, not blocking)
    etat = entry.get("etat_administratif")
    if etat:
        member.setdefault("data_gouv", {})["etat_administratif"] = etat
    cat = entry.get("categorie_entreprise")
    if cat:
        member.setdefault("data_gouv", {})["categorie_entreprise"] = cat

    # Address fallback (only if ORIAS pass didn't fill it)
    if not (member.get("address") or {}).get("street"):
        siege = entry.get("siege") or {}
        if siege.get("adresse"):
            from .base import extract_department
            from config import DEPARTMENTS, DEPT_TO_REGION
            postal = siege.get("code_postal") or ""
            dept = extract_department(postal)
            member["address"] = {
                "street": (siege.get("adresse") or "").replace(postal, "").replace(siege.get("libelle_commune", ""), "").strip(",. "),
                "postal_code": postal,
                "city": siege.get("libelle_commune") or "",
                "department": dept,
                "department_name": DEPARTMENTS.get(dept, ""),
                "region": DEPT_TO_REGION.get(dept, ""),
            }

    return member


def batch_enrich(members, max_lookups=None, log_every=50):
    """Enrich members missing dirigeants from data.gouv."""
    candidates = [m for m in members if m.get("siren") and not m.get("directors")]
    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"data.gouv enrich: {len(candidates)} members to enrich")

    found_dir = found_addr = 0
    for i, m in enumerate(candidates, 1):
        before_dir = bool(m.get("directors"))
        before_addr = bool((m.get("address") or {}).get("street"))

        enrich_member(m)

        if not before_dir and m.get("directors"):
            found_dir += 1
        if not before_addr and (m.get("address") or {}).get("street"):
            found_addr += 1

        if i % log_every == 0:
            logger.info(
                f"  data.gouv {i}/{len(candidates)} "
                f"(+{found_dir} dirigeants, +{found_addr} addr fallback)"
            )

    logger.info(
        f"data.gouv done: +{found_dir} dirigeants, +{found_addr} address fallbacks "
        f"(out of {len(candidates)} lookups)"
    )
    return members
