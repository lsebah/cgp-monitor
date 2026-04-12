"""
CGP Monitor - Folk CRM export.
Generates CSV files compatible with Folk CRM import format.
"""
import csv
import logging
import os

logger = logging.getLogger(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "folk_import.csv")

# Folk CRM import columns
FOLK_COLUMNS = [
    "First Name",
    "Last Name",
    "Job Title",
    "Company",
    "Email",
    "Phone",
    "Address",
    "City",
    "Postal Code",
    "Website",
    "Notes",
]


def export_new_members_csv(new_members, output_path=None):
    """
    Export new members to a CSV file compatible with Folk CRM import.

    Args:
        new_members: List of new member dicts
        output_path: Custom output path (default: docs/data/folk_import.csv)

    Returns:
        Path to the generated CSV file, or None if no new members
    """
    if not new_members:
        logger.info("Folk export: no new members to export")
        return None

    path = output_path or CSV_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)

    rows = []
    for member in new_members:
        # If we have directors, create a row per director
        directors = member.get("directors", [])
        if not directors:
            directors = [{"name": "", "role": ""}]

        for director in directors:
            # Split director name into first/last
            name_parts = director.get("name", "").strip().split(" ", 1)
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[1] if len(name_parts) > 1 else ""

            # Build notes
            notes_parts = []
            associations = list(member.get("associations", {}).keys())
            if associations:
                notes_parts.append(f"Associations: {', '.join(associations)}")
            activities = member.get("activities", [])
            if activities:
                notes_parts.append(f"Activites: {', '.join(activities)}")
            orias = member.get("orias_number", "")
            if orias:
                notes_parts.append(f"ORIAS: {orias}")
            siren = member.get("siren", "")
            if siren:
                notes_parts.append(f"SIREN: {siren}")
            first_seen = member.get("first_seen", "")
            if first_seen:
                notes_parts.append(f"Detecte: {first_seen}")
            notes_parts.append("Source: CGP Monitor")

            addr = member.get("address", {})
            address = addr.get("street", "")

            row = {
                "First Name": first_name,
                "Last Name": last_name,
                "Job Title": director.get("role", "Dirigeant"),
                "Company": member.get("company_name", ""),
                "Email": member.get("email", ""),
                "Phone": member.get("phone", ""),
                "Address": address,
                "City": addr.get("city", ""),
                "Postal Code": addr.get("postal_code", ""),
                "Website": member.get("website", ""),
                "Notes": " | ".join(notes_parts),
            }
            rows.append(row)

    # Write CSV
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FOLK_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Folk export: wrote {len(rows)} rows to {path}")
    return path
