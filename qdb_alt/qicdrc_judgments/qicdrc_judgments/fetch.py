"""Reading qicdrc.gov.qa politely.

Only what robots.txt allows: the sitemap and the English judgment pages. The
robots.txt is read at the start of every load and every URL is checked against
it before it is requested -- including where a redirect lands. The pause
between requests is never shorter than the Crawl-delay it asks for (10 s).
"""

from __future__ import annotations

import time

import requests

from .robots import Robots

ROOT = "https://www.qicdrc.gov.qa"
ROBOTS = ROOT + "/robots.txt"
SITEMAP = ROOT + "/sitemap.xml"
AGENT = "qdb-alt"          # the product token robots.txt groups are matched on
UA = {"User-Agent": "qdb-alt/qicdrc_judgments (+https://github.com/narensahu13/qdb-alt)"}


class SiteError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Disallowed(SiteError):
    """robots.txt says no: the URL is not requested at all."""


def use_system_trust_store() -> None:
    """Trust the Windows certificate store (a QDB proxy re-signs TLS); see icv/README."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


class Site:
    def __init__(self, delay: float = 10.0, timeout: float = 60, session=None):
        self.delay, self.timeout = delay, timeout
        self.http = session or requests.Session()
        self.robots: Robots | None = None
        self._last = 0.0

    def read_robots(self) -> Robots:
        """Fetch robots.txt and obey it from now on. A 4xx means there are no rules
        (RFC 9309, 2.3.1.3); anything else that is not a page stops the load."""
        try:
            text = self.get(ROBOTS)
        except SiteError as e:
            if e.status is None or not 400 <= e.status < 500:
                raise
            text = ""
        self.robots = Robots(text, AGENT)
        self.delay = max(self.delay, self.robots.crawl_delay or 0)
        return self.robots

    def _check(self, url: str) -> None:
        if self.robots is not None and not self.robots.allowed(url):
            raise Disallowed(f"{url}: disallowed by robots.txt")

    def get(self, url: str) -> str:
        self._check(url)
        time.sleep(max(0.0, self._last + self.delay - time.monotonic()))
        self._last = time.monotonic()
        try:
            r = self.http.get(url, headers=UA, timeout=self.timeout)
        except requests.RequestException as e:
            raise SiteError(f"{url}: {e}") from e
        if r.status_code != 200:
            raise SiteError(f"{url}: HTTP {r.status_code}", r.status_code)
        final = getattr(r, "url", None) or url
        if final != url:
            self._check(final)
        r.encoding = r.encoding or "utf-8"
        return r.text
