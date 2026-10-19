"""Reading the LEI records of Qatar from the GLEIF API.

api.gleif.org is GLEIF's public API, documented for programmatic use
(https://api.gleif.org/api/v1); its robots.txt disallows nothing
("User-agent: *", empty "Disallow:"). Still, one request at a time with a
pause between them. The records come 200 to a page (the API's maximum), about
two pages for Qatar.
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

API = "https://api.gleif.org/api/v1/lei-records"
PAGE_SIZE = 200
MAX_PAGES = 50          # Qatar has ~2 pages; a runaway lastPage is an error, not a crawl
UA = {"User-Agent": "qdb-alt/gleif_lei (+https://github.com/narensahu13/qdb-alt)",
      "Accept": "application/vnd.api+json"}


class SiteError(RuntimeError):
    pass


def page_url(number: int, country: str = "QA") -> str:
    return (f"{API}?filter[entity.legalAddress.country]={country}"
            f"&page[size]={PAGE_SIZE}&page[number]={number}")


class Site:
    def __init__(self, delay: float = 1.0, timeout: float = 60, session=None):
        self.delay, self.timeout = delay, timeout
        self.http = session or requests.Session()
        self._last = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        time.sleep(max(0.0, self._last + self.delay - time.monotonic()))
        self._last = time.monotonic()
        try:
            r = self.http.get(url, headers=UA, timeout=self.timeout)
        except requests.RequestException as e:
            raise SiteError(f"{url}: {e}") from e
        if r.status_code != 200:
            raise SiteError(f"{url}: HTTP {r.status_code}")
        r.encoding = "utf-8"                     # JSON is UTF-8; the media type carries no charset
        try:
            data = json.loads(r.text)
        except ValueError as e:
            raise SiteError(f"{url}: the answer is not JSON (an error page?)") from e
        if not isinstance(data, dict) or not isinstance(data.get("data"), list) or "meta" not in data:
            raise SiteError(f"{url}: the answer has no records (an error page?)")
        return data

    def records(self, country: str = "QA") -> list[dict[str, Any]]:
        """Every LEI record with its legal address in `country`, all pages read before returning.
        A failed page, a page count that runs away, or fewer records than the API announced
        raises SiteError."""
        out: dict[str, dict[str, Any]] = {}
        total, number, last = None, 1, 1
        while number <= last:
            data = self.get_json(page_url(number, country))
            pagination = data["meta"].get("pagination") or {}
            last, total = int(pagination.get("lastPage") or 1), pagination.get("total", total)
            if last > MAX_PAGES:
                raise SiteError(f"the API announces {last} pages; expected a few")
            for rec in data["data"]:
                out[rec.get("id") or rec["attributes"]["lei"]] = rec
            number += 1
        if total is not None and len(out) < int(total):
            raise SiteError(f"read {len(out)} records, the API announced {total}")
        return list(out.values())
