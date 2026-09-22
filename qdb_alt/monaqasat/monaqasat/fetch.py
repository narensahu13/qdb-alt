"""Polite HTTP.

The site sits behind a web application firewall that returns a 200 with a
"Request Rejected" body when it does not like you. So: a real browser
User-Agent, one session with cookies, a delay between requests, jitter, and
exponential backoff when a block is detected. Nothing here tries to defeat
the firewall -- if it keeps rejecting, the crawl stops and says so.

robots.txt on this host is `User-agent: * / Disallow:` -- nothing is
disallowed. The crawler re-checks that at startup and refuses to run if the
policy has changed.
"""

from __future__ import annotations

import os
import random
import ssl
import time
import urllib.robotparser
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter

BASE = "https://monaqasat.mof.gov.qa"
CULTURE_COOKIE = "c%3Den%7Cuic%3Den"
COOKIE_DOMAIN = "monaqasat.mof.gov.qa"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9,ar;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class Blocked(RuntimeError):
    """The request failed and retrying now will not help."""


class PageTimeout(Blocked):
    """The server did not answer in time.

    Register pages are rendered with every company's activity table inside
    them; page 2 (145 activity rows) took 30.8 s cold against the old 30 s
    timeout, which is the ReadTimeout seen in Sept 2026. A browser that shows
    it in 5 s is usually showing a cached copy. A timeout on one page says
    nothing about the next, so callers skip it, record it, and retry it on
    the next run.
    """


class _TLSAdapter(HTTPAdapter):
    """Mount a specific SSL context.

    Two things make Python fail on networks where a browser is fine: the
    corporate root that re-signs traffic lives in the Windows store and not
    in certifi, and OpenSSL 3 defaults are stricter than some older server
    stacks. Both are ordinary interoperability problems with ordinary fixes,
    and both are opt-in here. Nothing disables verification.
    """

    def __init__(self, context: ssl.SSLContext, **kw):
        self._ctx = context
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        kw["ssl_context"] = self._ctx
        return super().init_poolmanager(*a, **kw)

    def proxy_manager_for(self, *a, **kw):
        kw["ssl_context"] = self._ctx
        return super().proxy_manager_for(*a, **kw)


def _ctx_default() -> ssl.SSLContext:
    return ssl.create_default_context()


def _ctx_system_store() -> ssl.SSLContext | None:
    """The OS certificate store -- where a corporate root actually lives."""
    try:
        import truststore
    except ImportError:
        return None
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _ctx_compat() -> ssl.SSLContext:
    """Verified TLS, at OpenSSL's older security level.

    monaqasat.mof.gov.qa offers only RSA key-exchange ciphers such as
    AES128-GCM-SHA256. OpenSSL 3 will not negotiate those at its default
    security level, so the server finds no acceptable cipher and closes the
    connection without an alert -- which surfaces as a bare
    SSLZeroReturnError rather than a handshake failure.

    Dropping to level 1 lets that cipher be offered. Certificate
    verification is untouched: the server presents a valid DigiCert
    certificate and it is still checked. What is given up is forward
    secrecy, which is the server's configuration choice, not ours. For
    reading public pages with no credentials and no personal data in the
    request, that is an acceptable trade; it would not be for anything
    carrying secrets.
    """
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return ctx


def ssl_candidates() -> list[tuple[str, ssl.SSLContext | None]]:
    """Contexts to try, strongest first.

    MONAQASAT_TLS pins one instead of negotiating:
      default | system | compat | certifi
    """
    mode = os.getenv("MONAQASAT_TLS", "").lower()
    if mode == "compat":
        return [("compat", _ctx_compat())]
    if mode == "certifi":
        return [("certifi", None)]
    if mode == "system":
        return [("system store", _ctx_system_store() or _ctx_default())]
    if mode == "default":
        return [("default", _ctx_default())]

    out: list[tuple[str, ssl.SSLContext | None]] = [("default", None)]
    sys_ctx = _ctx_system_store()
    if sys_ctx is not None:
        out.append(("system certificate store", sys_ctx))
    out.append(("compat: verified TLS at OpenSSL security level 1", _ctx_compat()))
    return out


def decode_body(resp) -> str:
    """Response text, UTF-8 unless the server names another charset.

    requests falls back to ISO-8859-1 for text/html without a charset, which
    would turn every Arabic character into mojibake. The site serves UTF-8.
    """
    ctype = str(resp.headers.get("Content-Type", "")).lower()
    if "charset=" in ctype:
        charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip(" \"'")
        try:
            return resp.content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode(resp.apparent_encoding or "utf-8",
                                   errors="replace")


@dataclass
class Fetcher:
    base: str = BASE
    delay: float = 1.5           # seconds between requests
    jitter: float = 0.7          # +/- random seconds on top
    connect_timeout: float = 15.0
    read_timeout: float = 120.0  # some pages take a minute or more to render
    max_retries: int = 3
    timeout_retries: int = 2     # a page that hangs twice will hang again
    respect_robots: bool = True

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)

    def __post_init__(self) -> None:
        self.tls_mode = "not negotiated yet"
        self._candidates = ssl_candidates()
        self._new_session(self._candidates[0])
        self._last = 0.0
        self._robots: urllib.robotparser.RobotFileParser | None = None

    def _new_session(self, candidate) -> None:
        name, ctx = candidate
        self._candidate = candidate
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.session.cookies.set(
            ".AspNetCore.Culture", CULTURE_COOKIE, domain=COOKIE_DOMAIN)
        if ctx is not None:
            self.session.mount("https://", _TLSAdapter(ctx))
        self.tls_mode = name

    def negotiate(self, path: str = "/robots.txt") -> str:
        """Find a TLS setting this server accepts, and keep it.

        Tries the strictest first and steps down only as far as the server
        forces. Anything other than a TLS error is raised immediately -- a
        proxy or DNS problem is not something a cipher list will fix.
        """
        errors = []
        for candidate in self._candidates:
            self._new_session(candidate)
            try:
                resp = self.session.get(f"{self.base}{path}",
                                        timeout=self.timeout)
                resp.raise_for_status()
                return resp.text
            except requests.exceptions.SSLError as exc:
                errors.append(f"  {candidate[0]}: {str(exc)[:90]}")
                continue
            except requests.exceptions.ProxyError as exc:
                raise ConnectionError(
                    f"The proxy refused the connection to {self.base}.\n"
                    f"  {str(exc)[:160]}\n\n"
                    "Run  python -m monaqasat doctor  for the fix.") from exc
            except requests.exceptions.RequestException as exc:
                raise ConnectionError(
                    f"Could not reach {self.base}: {type(exc).__name__}: "
                    f"{str(exc)[:160]}\n\n"
                    "Run  python -m monaqasat doctor  for a diagnosis."
                ) from exc

        raise ConnectionError(
            f"No TLS setting worked for {self.base}. Tried:\n"
            + "\n".join(errors)
            + "\n\nRun  python -m monaqasat doctor  for a full diagnosis.")

    def _rebuild(self) -> None:
        """Fresh session and connection pool, same TLS setting.

        After a timeout or a dropped connection the pooled keep-alive socket
        is the usual suspect; retrying on it can hang again.
        """
        candidate = getattr(self, "_candidate", None) or self._candidates[0]
        try:
            self.session.close()
        except Exception:                          # noqa: BLE001, S110
            pass
        self._new_session(candidate)

    # -- robots ---------------------------------------------------------
    def check_robots(self) -> str:
        """Fetch and parse robots.txt. Raises if crawling is disallowed."""
        rp = urllib.robotparser.RobotFileParser()
        text = self.negotiate("/robots.txt")
        rp.parse(text.splitlines())
        self._robots = rp
        probe = f"{self.base}/TendersOnlineServices/AwardedTenders/1"
        if self.respect_robots and not rp.can_fetch(UA, probe):
            raise RuntimeError(
                "robots.txt now disallows the tender listings. Stopping.\n\n"
                + text)
        return text

    def allowed(self, url: str) -> bool:
        if not self.respect_robots or self._robots is None:
            return True
        return self._robots.can_fetch(UA, url)

    # -- fetch ----------------------------------------------------------
    def _sleep(self) -> None:
        wait = self.delay + random.uniform(-self.jitter, self.jitter)
        elapsed = time.monotonic() - self._last
        if elapsed < wait:
            time.sleep(max(0.0, wait - elapsed))
        self._last = time.monotonic()

    def get(self, path: str) -> tuple[str, int]:
        """GET a path, returning (html, status).

        Raises PageTimeout when the server does not answer in time, and
        Blocked for anything else that survives the retries.
        """
        from .parse import looks_rejected

        url = path if path.startswith("http") else f"{self.base}{path}"
        if not self.allowed(url):
            raise RuntimeError(f"robots.txt disallows {url}")

        backoff = 5.0
        timeouts = 0
        attempt = 0
        while True:
            attempt += 1
            self._sleep()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.exceptions.Timeout as exc:
                timeouts += 1
                if timeouts >= self.timeout_retries:
                    raise PageTimeout(
                        f"{url}: no answer within {self.read_timeout:.0f}s, "
                        f"{timeouts} attempts") from exc
                self._rebuild()
                time.sleep(backoff)
                backoff *= 2
                continue
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise Blocked(f"{url}: {type(exc).__name__}: "
                                  f"{str(exc)[:160]}") from exc
                self._rebuild()
                time.sleep(backoff)
                backoff *= 2
                continue

            text = decode_body(resp)
            if resp.status_code == 200 and not looks_rejected(text):
                return text, resp.status_code

            if attempt >= self.max_retries:
                reason = ("firewall rejected the request"
                          if looks_rejected(text)
                          else f"HTTP {resp.status_code}")
                raise Blocked(f"{url}: {reason} after {attempt} attempts. "
                              "Slow down (--delay), or try again later.")
            time.sleep(backoff)
            backoff *= 2

    # -- paths ----------------------------------------------------------
    @staticmethod
    def listing_path(kind_path: str, page: int) -> str:
        return f"/TendersOnlineServices/{kind_path}/{page}"

    @staticmethod
    def detail_path(tender_id: str) -> str:
        return f"/TendersOnlineServices/TenderCompaniesDetails/{tender_id}"
