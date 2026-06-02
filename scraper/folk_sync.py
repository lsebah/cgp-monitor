"""
Push Folk-marked cabinets to Folk CRM via the API (server-side, no CORS).

Reads the list of cabinet IDs to push from a JSON file (folk_push.json),
looks them up in members.json, and creates them as companies in Folk.
"""
import json, logging, os, sys, requests
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

FOLK_KEY = os.environ.get("FOLK_KEY", "")
BASE = "https://api.folk.app/v1"
H = {"Authorization": f"Bearer {FOLK_KEY}", "Content-Type": "application/json"}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
MEMBERS_PATH = os.path.join(DATA_DIR, "members.json")
PUSH_PATH = os.path.join(DATA_DIR, "folk_push.json")
GROUP_ID = os.environ.get("FOLK_GROUP", "")


def push_company(m):
    # Step 1: create company with name only (guaranteed to work)
    payload = {"name": m.get("company_name", "")}
    r = requests.post(BASE + "/companies", headers=H, json=payload, timeout=15)
    if not r.ok:
        return r
    company = r.json().get("data", {})
    cid = company.get("id")
    if not cid:
        return r
    # Step 2: patch with contact details (best effort)
    update = {}
    if m.get("phone"): update["phones"] = [m["phone"]]
    if m.get("email"): update["emails"] = [m["email"]]
    if m.get("website"):
        url = m["website"] if m["website"].startswith("http") else "https://" + m["website"]
        update["urls"] = [url]
    if update:
        r2 = requests.patch(BASE + f"/companies/{cid}", headers=H, json=update, timeout=15)
        if r2.ok:
            log.info(f"  Updated {cid} with {list(update.keys())}")
        else:
            log.warning(f"  PATCH {cid} failed ({r2.status_code}): {r2.text[:200]}")
    return r


def main():
    if not FOLK_KEY:
        log.error("FOLK_KEY not set"); return

    # Load push list
    try:
        push_ids = json.load(open(PUSH_PATH, encoding="utf-8"))
    except FileNotFoundError:
        log.warning("No folk_push.json found. Nothing to push.")
        return
    if not push_ids:
        log.warning("folk_push.json is empty. Nothing to push.")
        return

    members = {m["id"]: m for m in json.load(open(MEMBERS_PATH, encoding="utf-8")).get("members", [])}
    ok, err, skip = 0, 0, 0

    for mid in push_ids:
        m = members.get(mid)
        if not m:
            log.warning(f"  ID {mid} not found in members.json, skipping")
            skip += 1
            continue
        r = push_company(m)
        if r.status_code in (200, 201):
            log.info(f"  OK: {m['company_name']} -> {r.json().get('data',{}).get('id','')}")
            ok += 1
        elif r.status_code == 409:
            log.info(f"  EXISTS: {m['company_name']}")
            ok += 1
        else:
            log.warning(f"  FAIL: {m['company_name']} -> HTTP {r.status_code}: {r.text[:200]}")
            err += 1

    log.info(f"Done. {ok} pushed, {err} errors, {skip} skipped.")

    # Clear the push list after successful sync
    if err == 0:
        json.dump([], open(PUSH_PATH, "w", encoding="utf-8"))
        log.info("folk_push.json cleared.")


if __name__ == "__main__":
    main()
