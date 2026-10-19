"""Talking to qataronlinedirectory.com.

Two kinds of page are read, both allowed to every crawler by the site's
robots.txt (checked October 2026; only /register, /middleast and /dashboard
are closed):

    /search?Search_x=&company=<name>&category=      the company-name search
    /company-details/<id>/<slug>                     a listing (redirects to /companies/<slug>)

The same robots.txt asks for a crawl delay of 5 seconds, so the client waits
at least that long between any two requests, and it says who it is in the
User-Agent instead of posing as a browser.
"""

from __future__ import annotations

import time

import requests

BASE = "https://qataronlinedirectory.com"
SEARCH = BASE + "/search"
UA = {"User-Agent": "qdb-alt/qatar_online_directory (+https://github.com/narensahu13/qdb-alt)"}
PAUSE = 5.0          # robots.txt: Crawl-delay: 5


def use_system_trust_store() -> None:
    """Trust the Windows certificate store (a QDB proxy re-signs TLS); see icv/README."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


class Client:
    """A polite HTTP client: one session, our own User-Agent, a pause between requests.

    `session` is anything with requests' `get(url, params=, headers=, timeout=)`;
    the tests pass a fake one that serves the fixtures."""

    def __init__(self, session=None, pause: float = PAUSE, timeout: float = 60):
        self.http = session or requests.Session()
        self.pause = pause
        self.timeout = timeout
        self._last = 0.0
        self.requests = 0

    def get(self, url: str, params: dict | None = None) -> str:
        """The page's HTML; an HTTP error status raises."""
        wait = self._last + self.pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        self.requests += 1
        r = self.http.get(url, params=params, headers=UA, timeout=self.timeout)
        r.raise_for_status()
        return r.text

    def search(self, name: str, page: int = 1) -> str:
        """One page of the company-name search. The site's own pagination links drop
        the empty Search_x and category fields, so later pages are asked for the same way."""
        params = {"Search_x": "", "company": name, "category": ""} if page == 1 else {"company": name, "page": page}
        return self.get(SEARCH, params)

    def company(self, url: str) -> str:
        return self.get(url)
