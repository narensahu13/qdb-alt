"""Reading Mwani's list of licensed port operators.

mwani.com.qa is a SharePoint site; the companies shown on its "Shipping
Lines" page come from a SharePoint list, readable through the site's REST
endpoint (/_api/). robots.txt disallows /_layouts/, /_vti_bin/ and
/_catalogs/ only, so /_api/ is open. One request returns the whole list.
"""

from __future__ import annotations

import json

import requests

LIST_URL = ("https://www.mwani.com.qa/_api/Web/lists/GetByTitle('Shipping%20Lines')/items"
            "?$select=Id,Title,Title_Arabic,Category,Url,Created,Modified&$top=5000")
HEADERS = {"User-Agent": "qdb-alt/mwani_port_operators (+https://github.com/narensahu13/qdb-alt)",
           "Accept": "application/json;odata=nometadata"}


class SiteError(RuntimeError):
    pass


def use_system_trust_store() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def operators(session=None, timeout: float = 60) -> list[dict]:
    http = session or requests.Session()
    try:
        r = http.get(LIST_URL, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise SiteError(f"{LIST_URL}: {e}") from e
    if r.status_code != 200:
        raise SiteError(f"{LIST_URL}: HTTP {r.status_code}")
    try:
        rows = json.loads(r.text)["value"]
    except (ValueError, KeyError) as e:
        raise SiteError(f"{LIST_URL}: the answer is not the list ({e})") from e
    if not rows:
        raise SiteError(f"{LIST_URL}: the list is empty")
    return rows
