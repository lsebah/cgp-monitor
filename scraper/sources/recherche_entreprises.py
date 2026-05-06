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


def _norm(s):
    """Quick name normalizer for fuzzy matching SIREN-finder hits."""
    import re, unicodedata
    if not s:
        return ""
    s = unicodedata.normalize('NFKD', s.lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    # Drop legal suffixes
    s = re.sub(r'\b(sas|sasu|sarl|eurl|sa|sci|scp|selarl|selas)\b', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _looks_like_match(member, entry):
    """Confirm the API hit really is the same cabinet.

    Without this check, a search for "PATRIMOINE CONSEIL" with no SIREN can
    paste another firm's data onto the member.
    """
    member_postal = (member.get("address") or {}).get("postal_code") or ""
    entry_postal = (entry.get("siege") or {}).get("code_postal") or ""
    # Postal codes must match if we have one on either side
    if member_postal and entry_postal and member_postal != entry_postal:
        return False
    # Name must be a meaningful prefix/contains relation
    m_name = _norm(member.get("company_name", ""))
    e_name = _norm(entry.get("nom_complet") or entry.get("nom_raison_sociale") or "")
    if not m_name or not e_name:
        return False
    return m_name in e_name or e_name in m_name


def enrich_member(member):
    """Fetch dirigeants + creation_date from data.gouv API.

    Strategy: prefer SIREN lookup. If SIREN is missing, fall back to a
    name+postal_code search and validate before stamping the result on the
    member (also captures the SIREN itself for downstream ORIAS lookup).

    Mutates and returns the member dict.
    """
    siren = (member.get("siren") or "").strip()
    has_siren = siren and len(siren) == 9
    member_postal = (member.get("address") or {}).get("postal_code") or ""
    member_name = (member.get("company_name") or "").strip()

    # Skip only when ALL the data.gouv fields we care about are already filled.
    if member.get("directors") and member.get("creation_date") and has_siren:
        return member

    # Build the right query
    params = {"page": 1, "per_page": 1}
    if has_siren:
        params["q"] = siren
    else:
        if not member_name:
            return member
        params["q"] = member_name
        if member_postal:
            params["code_postal"] = member_postal
            params["per_page"] = 3  # widen a bit so we can pick the best match

    try:
        resp = fetch(API_URL, params=params, delay=0.2, max_retries=2)
        if not resp:
            return member
        payload = resp.json()
    except Exception as e:
        logger.debug(f"data.gouv fetch failed for {siren or member_name}: {e}")
        return member

    results = payload.get("results", [])
    if not results:
        return member

    if has_siren:
        entry = results[0]
        if entry.get("siren") != siren:
            return member
    else:
        # Pick first result that passes the name+postal validation
        entry = next((r for r in results if _looks_like_match(member, r)), None)
        if not entry:
            return member
        # Stamp the SIREN on the member so the ORIAS pass can use it next.
        if entry.get("siren"):
            member["siren"] = entry["siren"]

    # Directors. Merge with existing rather than overwrite, so LinkedIn URLs
    # added by linkedin_search.py on a previous run survive a data.gouv re-run.
    raw_dirigeants = entry.get("dirigeants") or []
    formatted = [d for d in (_format_director(rd) for rd in raw_dirigeants) if d]
    if formatted:
        existing = member.get("directors") or []
        existing_by_name = {(d.get("name") or "").lower(): d for d in existing}
        merged = []
        for d in formatted:
            key = (d.get("name") or "").lower()
            prior = existing_by_name.get(key)
            if prior:
                # Carry over fields the API doesn't set (linkedin, email, phone, etc.)
                for k, v in prior.items():
                    if k not in d and v:
                        d[k] = v
            merged.append(d)
        # Preserve any director we used to know about that data.gouv no longer lists
        # (e.g. if API lookup is partial)
        new_keys = {(d.get("name") or "").lower() for d in merged}
        for d in existing:
            if (d.get("name") or "").lower() not in new_keys:
                merged.append(d)
        member["directors"] = merged

    # Creation date (used by the "récents" UI filter). API returns YYYY-MM-DD.
    if entry.get("date_creation") and not member.get("creation_date"):
        member["creation_date"] = entry["date_creation"]

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
    """Enrich members missing dirigeants, creation_date, or SIREN from data.gouv.

    Now also covers members WITHOUT a SIREN — the API is searched by
    name + postal_code and the SIREN is captured if a confident match is
    found.
    """
    candidates = []
    for m in members:
        has_siren = bool(m.get("siren"))
        has_dirs = bool(m.get("directors"))
        has_cd = bool(m.get("creation_date"))
        # Need data.gouv if any of (siren, dirs, creation_date) is missing AND
        # we have either a SIREN to query by or a name+postal to look up.
        wants = (not has_siren) or (not has_dirs) or (not has_cd)
        can_query = has_siren or (m.get("company_name") and (m.get("address") or {}).get("postal_code"))
        if wants and can_query:
            candidates.append(m)
    if max_lookups is not None:
        candidates = candidates[:max_lookups]

    logger.info(f"data.gouv enrich: {len(candidates)} members to enrich")

    found_dir = found_addr = found_date = found_siren = 0
    for i, m in enumerate(candidates, 1):
        before_dir = bool(m.get("directors"))
        before_addr = bool((m.get("address") or {}).get("street"))
        before_date = bool(m.get("creation_date"))
        before_siren = bool(m.get("siren"))

        enrich_member(m)

        if not before_dir and m.get("directors"):
            found_dir += 1
        if not before_addr and (m.get("address") or {}).get("street"):
            found_addr += 1
        if not before_date and m.get("creation_date"):
            found_date += 1
        if not before_siren and m.get("siren"):
            found_siren += 1

        if i % log_every == 0:
            logger.info(
                f"  data.gouv {i}/{len(candidates)} "
                f"(+{found_siren} SIRENs, +{found_dir} dirigeants, +{found_date} dates)"
            )

    logger.info(
        f"data.gouv done: +{found_siren} SIRENs, +{found_dir} dirigeants, "
        f"+{found_date} creation_dates, +{found_addr} address fallbacks "
        f"(out of {len(candidates)} lookups)"
    )
    return members
