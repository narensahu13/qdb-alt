"""Reading the four papers politely.

Every host's robots.txt is read before its first page and every URL is
checked against it before it is requested -- including where a redirect
lands. A URL robots.txt does not allow is never requested. The pause between
two requests to the same host is never shorter than the Crawl-delay that host
asks for (The Peninsula: 10 s, Gulf Times: 2 s) nor than our own default
(2 s). QNA's sitemap index is on www.qna.org.qa and its sitemaps on
qna.org.qa: two hosts, two robots.txt files, both read.

A request that does not answer is tried once more after a longer pause
before it counts as failed. A 404 / 410 is not retried: the page is gone.
"""

from __future__ import annotations

import time
from urllib.parse import urlencode, urlsplit

import requests

from .robots import Robots

AGENT = "qdb-alt"          # the product token robots.txt groups are matched on
UA = {"User-Agent": "qdb-alt/qatar_news (+https://github.com/narensahu13/qdb-alt)"}
GT_SEARCH = "https://www.gulf-times.com/search"


class SiteError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PageGone(SiteError):
    """404 / 410: listed somewhere, but the site no longer has it."""


class Disallowed(SiteError):
    """robots.txt says no: the URL is not requested at all."""


def use_system_trust_store() -> None:
    """Trust the Windows certificate store (a QDB proxy re-signs TLS); see icv/README."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


def gt_search_url(query: str) -> str:
    """Gulf Times' own search, exact phrase -- allowed by its robots.txt."""
    return f"{GT_SEARCH}?{urlencode({'query': query, 'match': 'exact'})}"


class Site:
    """One polite reader for several hosts.

    `timer` is anything with `monotonic()` and `sleep(seconds)` -- the `time`
    module itself, or a stand-in clock in the tests."""

    def __init__(self, delay: float = 2.0, timeout: float = 120, retries: int = 1, session=None, timer=time):
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.http = session or requests.Session()
        self.timer = timer
        self.robots: dict[str, Robots] = {}
        self._unreadable: dict[str, SiteError] = {}
        self._last: dict[str, float] = {}

    # ---- robots.txt --------------------------------------------------------

    def robots_for(self, url: str) -> Robots:
        """The host's robots.txt, read once. A 4xx means there are no rules
        (RFC 9309, 2.3.1.3); a robots.txt that cannot be read means the host is
        not read at all (2.3.1.4)."""
        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host in self._unreadable:
            raise self._unreadable[host]
        if host not in self.robots:
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
            try:
                text = self._fetch(robots_url, host, self.delay).text
            except SiteError as e:
                if e.status is None or not 400 <= e.status < 500:
                    # remembered: the rest of this run does not ask again for every page of the host
                    self._unreadable[host] = SiteError(f"{robots_url} could not be read, so {host} is not read: "
                                                       f"{e}", e.status)
                    raise self._unreadable[host] from e
                text = ""
            self.robots[host] = Robots(text, AGENT)
        return self.robots[host]

    def allowed(self, url: str) -> bool:
        return self.robots_for(url).allowed(url)

    def delay_for(self, url: str) -> float:
        return max(self.delay, self.robots_for(url).crawl_delay or 0)

    def _check(self, url: str) -> None:
        if not self.allowed(url):
            raise Disallowed(f"{url}: disallowed by robots.txt")

    # ---- requests ----------------------------------------------------------

    def _fetch(self, url: str, host: str, delay: float):
        error: SiteError | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self.timer.sleep(delay * 5)
            self.timer.sleep(max(0.0, self._last.get(host, float("-inf")) + delay - self.timer.monotonic()))
            self._last[host] = self.timer.monotonic()
            try:
                r = self.http.get(url, headers=UA, timeout=self.timeout)
            except requests.RequestException as e:
                error = SiteError(f"{url}: {e}")
                continue
            if r.status_code == 200:
                return r
            if r.status_code in (404, 410):
                raise PageGone(f"{url}: HTTP {r.status_code}", r.status_code)
            error = SiteError(f"{url}: HTTP {r.status_code}", r.status_code)
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break                                   # asking again will not change a 403
        assert error is not None
        raise error

    def _response(self, url: str):
        self._check(url)
        r = self._fetch(url, urlsplit(url).netloc.lower(), self.delay_for(url))
        final = getattr(r, "url", None) or url
        if final != url:
            self._check(final)
        return r

    def get(self, url: str) -> str:
        r = self._response(url)
        r.encoding = r.encoding or "utf-8"
        return r.text

    def get_bytes(self, url: str) -> bytes:
        return self._response(url).content
