"""
CGP Monitor - Contact enrichment module.
Finds email addresses from company websites and enriches member data
from CNCEF detail pages (ORIAS numbers).
"""
import logging
import re

from bs4 import BeautifulSoup

from .base import fetch, clean_email

logger = logging.getLogger(__name__)

# Common email patterns to ignore
IGNORE_EMAILS = {
    "contact@cncef.org", "info@cncef.org", "contact@cncgp.fr",
    "contact@anacofi.asso.fr", "webmaster@", "admin@",
}


def enrich_email_from_website(member):
    """
    Try to find the contact email by visiting the member's website.

    Args:
        member: Member dict with 'website' field

    Returns:
        Updated member dict
    """
    website = member.get("website", "")
    if not website or member.get("email"):
        return member

    # Normalize URL
    if not website.startswith("http"):
        website = "https://" + website

    try:
        resp = fetch(website, delay=0.5, max_retries=1)
        if not resp:
            return member

        text = resp.text
        # Find all emails on the page
        emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)

        # Filter and prioritize
        valid_emails = []
        for email in emails:
            email = email.lower()
            if any(ignore in email for ignore in IGNORE_EMAILS):
                continue
            if email.endswith(('.png', '.jpg', '.gif', '.svg', '.css', '.js')):
                continue
            cleaned = clean_email(email)
            if cleaned:
                valid_emails.append(cleaned)

        if valid_emails:
            # Prioritize contact/info emails
            for priority in ['contact@', 'info@', 'accueil@', 'cabinet@']:
                for e in valid_emails:
                    if e.startswith(priority):
                        member["email"] = e
                        return member
            # Otherwise take first
            member["email"] = valid_emails[0]

    except Exception as e:
        logger.debug(f"Email enrichment failed for {website}: {e}")

    return member


def enrich_from_cncef_detail(member, detail_url):
    """
    Enrich member from CNCEF detail page (ORIAS, specialties, region).

    Args:
        member: Member dict
        detail_url: URL of the CNCEF detail page

    Returns:
        Updated member dict
    """
    if not detail_url:
        return member

    try:
        resp = fetch(detail_url, delay=1.0, max_retries=1)
        if not resp:
            return member

        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)

        # ORIAS number
        if not member.get("orias_number"):
            orias_match = re.search(r'ORIAS\s*:?\s*(\d{8})', text, re.I)
            if orias_match:
                member["orias_number"] = orias_match.group(1)

        # Phone from detail page
        if not member.get("phone"):
            phone_el = soup.select_one('a[href^="tel:"]')
            if phone_el:
                member["phone"] = phone_el.get("href", "").replace("tel:", "").strip()

        # Email from detail page
        if not member.get("email"):
            email_el = soup.select_one('a[href^="mailto:"]')
            if email_el:
                email = email_el.get("href", "").replace("mailto:", "").strip()
                if "subject=" not in email:  # skip share mailto links
                    member["email"] = clean_email(email)

    except Exception as e:
        logger.debug(f"CNCEF detail enrichment failed for {detail_url}: {e}")

    return member


def batch_enrich_emails(members, max_lookups=200, max_workers=8):
    """
    Batch enrich emails from company websites in parallel.

    Args:
        members: List of member dicts (modified in place)
        max_lookups: Maximum website lookups per run
        max_workers: Concurrent fetches (8 keeps polite pressure on small sites)

    Returns:
        Updated member list
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    candidates = [m for m in members if not m.get("email") and m.get("website")][:max_lookups]
    if not candidates:
        logger.info("Email enrichment: nothing to enrich")
        return members

    found = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        # enrich_email_from_website mutates the dict and returns it; we just
        # need to make sure each fetch eventually completes or is bounded.
        future_to_member = {ex.submit(enrich_email_from_website, m): m for m in candidates}
        for fut in as_completed(future_to_member):
            try:
                m = fut.result(timeout=45)  # hard cap per fetch (fetch itself is 20s, retries 1)
                if m.get("email"):
                    found += 1
            except Exception as e:
                logger.debug(f"Enrich worker failed: {e}")

    logger.info(f"Email enrichment: {found}/{len(candidates)} emails found from websites")
    return members
