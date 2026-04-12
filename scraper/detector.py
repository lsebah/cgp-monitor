"""
CGP Monitor - New member detection.
Compares current scrape results against previous data to identify new members.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Members not seen for this many days are marked as removed
REMOVAL_THRESHOLD_DAYS = 30


def detect_changes(existing_members, current_members, today):
    """
    Compare current scrape with existing data to detect new and removed members.

    Args:
        existing_members: List of member dicts from previous data
        current_members: List of member dicts from current scrape
        today: Today's date string (YYYY-MM-DD)

    Returns:
        Tuple of (merged_members, new_members) where:
        - merged_members: Full updated member list
        - new_members: Only newly detected members
    """
    existing_map = {m["id"]: m for m in existing_members}
    current_ids = set()
    merged = {}
    new_members = []

    for member in current_members:
        mid = member["id"]
        current_ids.add(mid)

        if mid in existing_map:
            # Known member - preserve first_seen, update last_seen
            existing = existing_map[mid]
            member["first_seen"] = existing.get("first_seen", today)
            member["is_new"] = False
            member["last_seen"] = today
            # Preserve contacted state from existing
            if existing.get("contacted"):
                member["contacted"] = existing["contacted"]
        else:
            # New member
            member["first_seen"] = today
            member["last_seen"] = today
            member["is_new"] = True
            new_members.append(member)

        merged[mid] = member

    # Keep existing members not in current scrape (may still be active)
    for mid, member in existing_map.items():
        if mid not in merged:
            last_seen = member.get("last_seen", today)
            try:
                days_since = (datetime.strptime(today, "%Y-%m-%d") -
                              datetime.strptime(last_seen, "%Y-%m-%d")).days
            except ValueError:
                days_since = 0

            if days_since <= REMOVAL_THRESHOLD_DAYS:
                member["is_new"] = False
                merged[mid] = member
            else:
                # Mark as removed but keep in data
                member["is_new"] = False
                member["status"] = "removed"
                merged[mid] = member

    result = list(merged.values())
    logger.info(f"Detection: {len(result)} total, {len(new_members)} new, "
                f"{len(existing_map)} existing, {len(current_ids)} current")

    return result, new_members


def build_new_members_data(new_members, today):
    """
    Build the new_members.json data structure.

    Args:
        new_members: List of newly detected member dicts
        today: Today's date string

    Returns:
        Dict for new_members.json
    """
    alerts = []
    for m in new_members:
        alerts.append({
            "id": m["id"],
            "company_name": m.get("company_name", ""),
            "city": m.get("address", {}).get("city", ""),
            "department": m.get("address", {}).get("department", ""),
            "activities": m.get("activities", []),
            "associations": list(m.get("associations", {}).keys()),
            "first_seen": m.get("first_seen", today),
            "phone": m.get("phone", ""),
            "email": m.get("email", ""),
        })

    # Sort by date, most recent first
    alerts.sort(key=lambda x: x.get("first_seen", ""), reverse=True)

    return {
        "last_updated": today,
        "count": len(alerts),
        "new_members": alerts,
    }


def build_stats(members, new_count, today):
    """
    Build aggregate statistics.

    Args:
        members: Full member list
        new_count: Number of new members detected today
        today: Today's date string

    Returns:
        Dict for stats
    """
    active = [m for m in members if m.get("status") != "removed"]

    # By association
    by_association = {}
    for m in active:
        for assoc in m.get("associations", {}):
            by_association[assoc] = by_association.get(assoc, 0) + 1

    # By activity
    by_activity = {}
    for m in active:
        for act in m.get("activities", []):
            by_activity[act] = by_activity.get(act, 0) + 1

    # By department
    by_department = {}
    for m in active:
        dept = m.get("address", {}).get("department", "")
        if dept:
            by_department[dept] = by_department.get(dept, 0) + 1

    # New this week/month
    new_this_week = 0
    new_this_month = 0
    try:
        today_dt = datetime.strptime(today, "%Y-%m-%d")
        for m in active:
            fs = m.get("first_seen", "")
            if not fs:
                continue
            try:
                fs_dt = datetime.strptime(fs, "%Y-%m-%d")
                days = (today_dt - fs_dt).days
                if days <= 7:
                    new_this_week += 1
                if days <= 30:
                    new_this_month += 1
            except ValueError:
                pass
    except ValueError:
        pass

    return {
        "total_members": len(active),
        "new_today": new_count,
        "new_this_week": new_this_week,
        "new_this_month": new_this_month,
        "by_association": by_association,
        "by_activity": by_activity,
        "by_department": dict(sorted(by_department.items(), key=lambda x: -x[1])[:20]),
        "total_departments": len(by_department),
    }
