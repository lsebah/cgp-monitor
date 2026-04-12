"""
CGP Monitor - Cross-source deduplication and merging.
Merges members from CNCEF, CNCGP, ANACOFI into a unified directory.
"""
import logging
from difflib import SequenceMatcher

from sources.base import normalize_name, normalize_city, clean_siren

logger = logging.getLogger(__name__)


def merge_all_sources(*source_lists):
    """
    Merge member lists from multiple sources into a single deduplicated list.

    Deduplication strategy:
    1. Exact SIREN match -> merge
    2. Exact normalized name + same city -> merge
    3. Fuzzy name match (>0.85) + same department -> merge
    4. No match -> new entry

    Args:
        *source_lists: Variable number of member lists

    Returns:
        List of merged member dicts
    """
    merged = {}  # id -> member
    siren_index = {}  # siren -> id
    name_city_index = {}  # (normalized_name, city) -> id
    name_dept_index = {}  # (normalized_name, dept) -> [ids]

    total_input = 0
    merge_count = 0

    for members in source_lists:
        for member in members:
            total_input += 1
            siren = member.get("siren", "")
            norm_name = member.get("company_name_normalized") or normalize_name(member.get("company_name", ""))
            city = normalize_city(member.get("address", {}).get("city", ""))
            dept = member.get("address", {}).get("department", "")

            matched_id = None

            # Strategy 1: Exact SIREN match
            if siren and siren in siren_index:
                matched_id = siren_index[siren]

            # Strategy 2: Exact normalized name + city
            if not matched_id and norm_name and city:
                key = (norm_name, city)
                if key in name_city_index:
                    matched_id = name_city_index[key]

            # Strategy 3: Fuzzy name + same department
            if not matched_id and norm_name and dept:
                dept_key = dept
                if dept_key in name_dept_index:
                    for candidate_id in name_dept_index[dept_key]:
                        candidate = merged[candidate_id]
                        candidate_name = candidate.get("company_name_normalized", "")
                        if _fuzzy_match(norm_name, candidate_name, threshold=0.85):
                            matched_id = candidate_id
                            break

            if matched_id:
                # Merge into existing
                _merge_member(merged[matched_id], member)
                merge_count += 1
            else:
                # New entry
                mid = member["id"]
                merged[mid] = member.copy()

                # Update indexes
                if siren:
                    siren_index[siren] = mid
                if norm_name and city:
                    name_city_index[(norm_name, city)] = mid
                if dept:
                    if dept not in name_dept_index:
                        name_dept_index[dept] = []
                    name_dept_index[dept].append(mid)

    logger.info(f"Merger: {total_input} input -> {len(merged)} unique ({merge_count} merged)")
    return list(merged.values())


def _merge_member(existing, new):
    """Merge new member data into existing member, filling gaps."""
    # Merge associations
    for source, data in new.get("associations", {}).items():
        if source not in existing.get("associations", {}):
            existing.setdefault("associations", {})[source] = data

    # Merge source URLs
    for source, url in new.get("source_urls", {}).items():
        if source not in existing.get("source_urls", {}):
            existing.setdefault("source_urls", {})[source] = url

    # Fill missing fields (existing takes precedence)
    _fill_field(existing, new, "siren")
    _fill_field(existing, new, "orias_number")
    _fill_field(existing, new, "phone")
    _fill_field(existing, new, "email")
    _fill_field(existing, new, "website")

    # Address: fill missing parts
    existing_addr = existing.get("address", {})
    new_addr = new.get("address", {})
    for key in ["street", "postal_code", "city", "department", "department_name", "region"]:
        if not existing_addr.get(key) and new_addr.get(key):
            existing_addr[key] = new_addr[key]
    existing["address"] = existing_addr

    # Merge activities (union)
    existing_acts = set(existing.get("activities", []))
    existing_acts.update(new.get("activities", []))
    existing["activities"] = list(existing_acts)

    # Merge specialties (union)
    existing_specs = set(existing.get("specialties", []))
    existing_specs.update(new.get("specialties", []))
    existing["specialties"] = list(existing_specs)

    # Merge directors (avoid duplicates by name)
    existing_dirs = {d.get("name", "").lower(): d for d in existing.get("directors", [])}
    for d in new.get("directors", []):
        key = d.get("name", "").lower()
        if key and key not in existing_dirs:
            existing_dirs[key] = d
    existing["directors"] = list(existing_dirs.values())


def _fill_field(existing, new, field):
    """Fill a field in existing from new if existing is empty."""
    if not existing.get(field) and new.get(field):
        existing[field] = new[field]


def _fuzzy_match(name1, name2, threshold=0.85):
    """Check if two names match with fuzzy string comparison."""
    if not name1 or not name2:
        return False
    ratio = SequenceMatcher(None, name1, name2).ratio()
    return ratio >= threshold
