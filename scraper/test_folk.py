"""Test Folk API: verify endpoints, payload format, and create a test contact."""
import logging, os, json, requests
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

KEY = os.environ.get("FOLK_KEY", "")
BASE = "https://api.folk.app/v1"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

def test_endpoint(method, path, body=None):
    url = BASE + path
    log.info(f"{method} {url}")
    try:
        if method == "GET":
            r = requests.get(url, headers=H, timeout=15)
        else:
            r = requests.request(method, url, headers=H, json=body, timeout=15)
        log.info(f"  -> {r.status_code}")
        log.info(f"  -> {r.text[:500]}")
        return r
    except Exception as e:
        log.error(f"  -> ERROR: {e}")
        return None

# 1. List groups
test_endpoint("GET", "/groups")

# 2. List people (1)
test_endpoint("GET", "/people?limit=1")

# 3. List companies (1)
test_endpoint("GET", "/companies?limit=1")

# 4. Try creating a person (test contact)
test_endpoint("POST", "/people", {
    "name": "TEST CGP Monitor",
    "emails": [{"value": "test@cgp-monitor.test", "label": "Work"}],
    "phones": [{"value": "+33100000000", "label": "Work"}],
})

# 5. Try creating a company
test_endpoint("POST", "/companies", {
    "name": "TEST CGP Monitor SAS",
})

# 6. Try alternate people endpoint formats
for path in ["/people", "/contacts", "/persons"]:
    test_endpoint("GET", path + "?limit=1")

log.info("Done.")
