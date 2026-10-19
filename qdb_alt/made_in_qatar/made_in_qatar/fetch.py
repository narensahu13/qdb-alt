"""Reading madeinqatar.qa politely.

Only what robots.txt allows: the sitemap and the factory pages (not /wp-json/).
One request at a time with a pause between them, and pages are fetched again
only when the sitemap says they changed.
"""

from __future__ import annotations

import time

import requests

SITEMAP = "https://madeinqatar.qa/factories-sitemap.xml"
UA = {"User-Agent": "qdb-alt/made_in_qatar (+https://github.com/narensahu13/qdb-alt)"}


class SiteError(RuntimeError):
    pass


def use_system_trust_store() -> None:
    """Trust the Windows certificate store (a QDB proxy re-signs TLS); see icv/README."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


class Site:
    def __init__(self, delay: float = 2.0, timeout: float = 60, session=None):
        self.delay, self.timeout = delay, timeout
        self.http = session or requests.Session()
        self._last = 0.0

    def get(self, url: str) -> str:
        time.sleep(max(0.0, self._last + self.delay - time.monotonic()))
        self._last = time.monotonic()
        try:
            r = self.http.get(url, headers=UA, timeout=self.timeout)
        except requests.RequestException as e:
            raise SiteError(f"{url}: {e}") from e
        if r.status_code != 200:
            raise SiteError(f"{url}: HTTP {r.status_code}")
        r.encoding = r.encoding or "utf-8"
        return r.text
