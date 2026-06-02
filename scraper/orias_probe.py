"""
Diagnostic probe: discover the ORIAS PUBLIC search mechanism (no login).
Runs on a GitHub runner (which can reach orias.fr) and logs the form
structure / endpoints so we can implement a credential-free CIF search.
Not part of the production pipeline.
"""
import logging
import re
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

CANDIDATES = [
    "https://www.orias.fr/search",
    "https://www.orias.fr/home/search",
    "https://www.orias.fr/web/guest/search",
    "https://www.orias.fr/home/resultSearch",
]


def probe():
    s = requests.Session()
    s.headers.update(HEADERS)
    for url in CANDIDATES:
        try:
            r = s.get(url, timeout=45, allow_redirects=True)
            log.info(f"GET {url} -> {r.status_code}, final={r.url}, {len(r.content)}b, ctype={r.headers.get('Content-Type','')}")
            if r.status_code != 200 or b"<form" not in r.content[:200000]:
                continue
            soup = BeautifulSoup(r.content, "lxml")
            title = soup.title.get_text(strip=True) if soup.title else ""
            log.info(f"   title={title!r}")
            for i, form in enumerate(soup.find_all("form")):
                action = form.get("action") or "(none)"
                method = (form.get("method") or "get").upper()
                names = []
                for inp in form.find_all(["input", "select", "textarea"]):
                    nm = inp.get("name")
                    if nm:
                        names.append(f"{nm}[{inp.get('type') or inp.name}]")
                log.info(f"   form#{i}: {method} action={action} fields={names[:25]}")
        except Exception as e:
            log.warning(f"GET {url} failed: {e}")


if __name__ == "__main__":
    probe()
