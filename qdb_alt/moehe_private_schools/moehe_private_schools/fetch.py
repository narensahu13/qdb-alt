"""Reading the Ministry of Education's map layer of private schools.

The "My Schools" map (myschools.edu.gov.qa) is served by ArcGIS; layer 1 of
the SchoolsEN service holds every licensed private school and kindergarten.
The layer is read with the standard ArcGIS query, 1,000 records a page,
asking only for the fields that are kept (no phones or e-mail addresses).

ArcGIS reports a failure as HTTP 200 with {"error": ...}, so an answer
without "features" is a failure, and the number of records read must equal
the number the layer reports (`returnCountOnly`). Either way the load stops
and the stored list stays as it was. There is no robots.txt on the host.
"""

from __future__ import annotations

import json
import time

import requests

LAYER_URL = "https://myschools.edu.gov.qa/server/rest/services/V2/SchoolsEN/MapServer/1"
QUERY_URL = f"{LAYER_URL}/query"
FIELDS = ("OBJECTID", "NAME", "ANAME", "CATEGORY", "ADOPTEDCURRICULUM", "DISTRICT_NAME_EN", "MNCP_NAME_EN",
          "ZONENUM", "OperationalStatus")
PAGE = 1000          # records per request; below the layer's limit of 2,000
PAUSE = 1.0          # seconds between requests
HEADERS = {"User-Agent": "qdb-alt/moehe_private_schools (+https://github.com/narensahu13/qdb-alt)",
           "Accept": "application/json"}


class SiteError(RuntimeError):
    pass


def use_system_trust_store() -> None:
    """The server sends its own certificate without the DigiCert intermediate;
    Windows completes the chain, the certificate bundle shipped with requests
    does not (see the README)."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def _query(http, params: dict, timeout: float) -> dict:
    try:
        r = http.get(QUERY_URL, params=params, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise SiteError(f"{QUERY_URL}: {e}") from e
    if r.status_code != 200:
        raise SiteError(f"{QUERY_URL}: HTTP {r.status_code}")
    try:
        data = json.loads(r.text)
    except ValueError as e:
        raise SiteError(f"{QUERY_URL}: the answer is not JSON ({e})") from e
    if not isinstance(data, dict):
        raise SiteError(f"{QUERY_URL}: unexpected answer")
    if "error" in data:
        raise SiteError(f"{QUERY_URL}: the server reported an error: {data['error']}")
    return data


def count(http, timeout: float = 60) -> int:
    n = _query(http, {"where": "1=1", "returnCountOnly": "true", "f": "json"}, timeout).get("count")
    if not isinstance(n, int):
        raise SiteError(f"{QUERY_URL}: no record count in the answer")
    return n


def schools(session=None, timeout: float = 60) -> list[dict]:
    """Every record of the layer, as ArcGIS attribute dicts."""
    http = session or requests.Session()
    try:
        total = count(http, timeout)
        rows: list[dict] = []
        while True:
            time.sleep(PAUSE)
            data = _query(http, {"where": "1=1", "outFields": ",".join(FIELDS), "f": "json",
                                 "returnGeometry": "false", "orderByFields": "OBJECTID",
                                 "resultOffset": len(rows), "resultRecordCount": PAGE}, timeout)
            features = data.get("features")
            if not isinstance(features, list):
                raise SiteError(f"{QUERY_URL}: no features in the answer at record {len(rows)}")
            rows += [f.get("attributes") or {} for f in features]
            if len(rows) > total:
                raise SiteError(f"{QUERY_URL}: {len(rows)} records read but the layer reports {total}")
            if len(features) < PAGE:
                break
    finally:
        if session is None:
            http.close()
    if not rows:
        raise SiteError(f"{QUERY_URL}: the layer is empty")
    if len(rows) != total:
        raise SiteError(f"{QUERY_URL}: {len(rows)} records read but the layer reports {total}")
    return rows
