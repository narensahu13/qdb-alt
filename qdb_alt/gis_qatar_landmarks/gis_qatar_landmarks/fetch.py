"""Reading the landmarks layer of GIS Qatar.

services.gisqatar.org.qa is the ArcGIS server of the Ministry of Municipality;
the Vector/Landmarks layer holds about 7,200 named places -- businesses,
hotels, schools, clinics, banks, mosques, government offices -- each a point
on the national map. The layer is read with the standard ArcGIS query, 1,000
records a page, asking only for the fields that are kept (no phones or e-mail
addresses) and for the point in latitude and longitude (`outSR=4326`).

ArcGIS reports a failure as HTTP 200 with {"error": ...}, so an answer
without "features" is a failure, and the number of records read must equal
the number the layer reports (`returnCountOnly`). Either way the load stops
and the stored layer stays as it was. There is no robots.txt on the host.
"""

from __future__ import annotations

import json
import time

import requests

LAYER_URL = "https://services.gisqatar.org.qa/server/rest/services/Vector/Landmarks/MapServer/0"
QUERY_URL = f"{LAYER_URL}/query"
FIELDS = ("LANDMARK_ID", "ENAME", "ANAME", "CATEGORY", "SUBCATEGORY_ANAME", "DISTRICT_ENAME", "ZONE_NO",
          "STATUS", "WEBID", "DATE_MODFD")
PAGE = 1000          # records per request; below the layer's limit of 10,000
PAUSE = 1.0          # seconds between requests
HEADERS = {"User-Agent": "qdb-alt/gis_qatar_landmarks (+https://github.com/narensahu13/qdb-alt)",
           "Accept": "application/json"}


class SiteError(RuntimeError):
    pass


def use_system_trust_store() -> None:
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


def landmarks(session=None, timeout: float = 60) -> list[dict]:
    """Every record of the layer: ArcGIS features, {"attributes": {...}, "geometry": {"x": lon, "y": lat}}."""
    http = session or requests.Session()
    try:
        total = count(http, timeout)
        rows: list[dict] = []
        while True:
            time.sleep(PAUSE)
            data = _query(http, {"where": "1=1", "outFields": ",".join(FIELDS), "f": "json",
                                 "returnGeometry": "true", "outSR": "4326", "orderByFields": "OBJECTID",
                                 "resultOffset": len(rows), "resultRecordCount": PAGE}, timeout)
            features = data.get("features")
            if not isinstance(features, list):
                raise SiteError(f"{QUERY_URL}: no features in the answer at record {len(rows)}")
            rows += features
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
