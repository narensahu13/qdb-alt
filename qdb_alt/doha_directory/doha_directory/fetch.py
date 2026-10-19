"""Reading www.doha.directory politely.

robots.txt allows everything (`User-agent: *` / `Allow: /`) and names the
sitemap. One request at a time with a pause between them, and a listing page
is fetched again only when the sitemap says it changed. A page that does not
answer is tried once more after a longer pause before it counts as failed.
"""

from __future__ import annotations

import gzip
import time

import requests

SITEMAP = "https://www.doha.directory/sitemap.xml.gz"
UA = {"User-Agent": "qdb-alt/doha_directory (+https://github.com/narensahu13/qdb-alt)"}


class SiteError(RuntimeError):
    pass


class PageGone(SiteError):
    """404 / 410: the sitemap still lists a page the site has removed."""


def use_system_trust_store() -> None:
    """Trust the Windows certificate store (a QDB proxy re-signs TLS); see icv/README."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


class Site:
    def __init__(self, delay: float = 2.0, timeout: float = 60, retries: int = 1, session=None):
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.http = session or requests.Session()
        self._last = 0.0

    def _response(self, url: str):
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(self.delay * 5)
            time.sleep(max(0.0, self._last + self.delay - time.monotonic()))
            self._last = time.monotonic()
            try:
                r = self.http.get(url, headers=UA, timeout=self.timeout)
            except requests.RequestException as e:
                error: SiteError = SiteError(f"{url}: {e}")
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (404, 410):
                raise PageGone(f"{url}: HTTP {r.status_code}")
            error = SiteError(f"{url}: HTTP {r.status_code}")
        raise error

    def get(self, url: str) -> str:
        r = self._response(url)
        r.encoding = r.encoding or "utf-8"
        return r.text

    def sitemap(self, url: str = SITEMAP) -> str:
        """The sitemap is served as a .gz file (not as a compressed response), so it is unpacked here."""
        data = self._response(url).content
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data.decode("utf-8")
