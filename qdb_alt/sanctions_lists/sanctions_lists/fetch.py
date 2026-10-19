"""Downloading the official lists, each from the authority that publishes it.

None of these hosts disallows the files in robots.txt (UN, OFAC and EU have no
robots.txt; the UK host refuses /robots.txt itself, which under RFC 9309 means
no rules). They are published precisely to be downloaded and screened against.

Every file is checked for what it should start and end with, and against the
Content-Length the server announced, so an error page or a download cut off
half-way is never read as a shorter list. (OFAC's files end with an
end-of-file control character, the UN's with its closing tag.)
"""

from __future__ import annotations

import time

import requests

UA = {"User-Agent": "qdb-alt/sanctions_lists (+https://github.com/narensahu13/qdb-alt)"}
OFAC = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/"
EOF = "\x1a"  # the end-of-file character every OFAC export ends with
FILES = {  # key: (url, what the file must start with, what it must end with)
    "un": ("https://scsanctions.un.org/resources/xml/en/consolidated.xml", "<?xml", "</CONSOLIDATED_LIST>"),
    "ofac_sdn": (OFAC + "SDN.CSV", None, EOF),
    "ofac_sdn_alt": (OFAC + "ALT.CSV", None, EOF),
    "ofac_cons": (OFAC + "CONS_PRIM.CSV", None, EOF),
    "ofac_cons_alt": (OFAC + "CONS_ALT.CSV", None, EOF),
    # the public token is the one the EU's Financial Sanctions Files page itself links with
    "eu": ("https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
           "fileGenerationDate;", None),
    "uk": ("https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv", "Report Date", None),
}


class SiteError(RuntimeError):
    pass


def use_system_trust_store() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def download(key: str, session=None, timeout: float = 600, attempts: int = 3, wait: float = 15) -> str:
    """One list file. A connection that fails or times out is tried again (3 attempts, 15 s apart);
    what comes back is then checked as described above."""
    url, starts, ends = FILES[key]
    http = session or requests.Session()
    for attempt in range(1, attempts + 1):
        try:
            r = http.get(url, headers=UA, timeout=(30, timeout))
            break
        except requests.RequestException as e:
            if attempt == attempts:
                raise SiteError(f"{key}: {e}") from e
            time.sleep(wait)
    if r.status_code != 200:
        raise SiteError(f"{key}: HTTP {r.status_code}")
    expected = r.headers.get("Content-Length")
    if expected and expected.isdigit() and int(expected) != len(r.content):
        raise SiteError(f"{key}: download cut off ({len(r.content)} of {expected} bytes)")
    text = r.content.decode("utf-8", errors="replace").lstrip("\ufeff")
    if ends and not text.rstrip().endswith(ends):
        raise SiteError(f"{key}: download cut off (the file does not end the way the list does)")
    if starts and not text.startswith(starts):
        raise SiteError(f"{key}: the file does not look like the list ({text[:60]!r})")
    body = text.strip().rstrip(EOF).strip()
    if starts is None and body and not body[:1].isdigit():   # OFAC CSVs start with an entity number (or are empty)
        raise SiteError(f"{key}: the file does not look like the list ({text[:60]!r})")
    return text
