"""Talking to the ICV portal.

Power Pages wants three things together: a session cookie, a
`__RequestVerificationToken` header that matches it, and a signed
`base64SecureConfiguration` lifted from the page the grid lives on. Miss any
one and the service answers 403 with no explanation, so everything here goes
through one session and every request carries the token from the most recent
page.

The site is a public register read by suppliers and buyers; this reads it at
one page every DELAY seconds and identifies itself. Nothing here signs in,
posts, or changes anything on the portal -- every call is a read.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import urljoin

import requests

from . import parse

BASE = "https://icv.tawteen.com.qa"
LISTING_PATH = "/en-US/supplier-database-guest/"
DETAIL_PATH = "/supplier-score-history/"

DELAY = 1.5              # seconds between requests
TIMEOUT = 60
RETRIES = 3
PAGE_SIZE = 1000         # the listing is ~6,900 rows, so 7 requests
HISTORY_PAGE_SIZE = 200  # more than any company's history

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36 "
      "qdb-alt/icv (QDB alternate-data research)")


class PortalError(RuntimeError):
    pass


class Portal:
    def __init__(self, base: str = BASE, delay: float = DELAY,
                 timeout: int = TIMEOUT, retries: int = RETRIES,
                 session: requests.Session | None = None):
        self.base = base.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.token: str | None = None
        self.requests_made = 0
        self._last = 0.0

    # ------------------------------------------------------------- basics

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def _send(self, method: str, url: str, **kw) -> requests.Response:
        last: Exception | None = None
        for attempt in range(self.retries):
            self._wait()
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as exc:
                last = exc
            else:
                self.requests_made += 1
                if r.status_code < 400:
                    return r
                # 429 and 5xx are worth another go; 4xx otherwise is not.
                if r.status_code != 429 and r.status_code < 500:
                    raise PortalError(
                        f"{method} {url} -> HTTP {r.status_code}"
                        + (" (the portal rejected the verification token or "
                           "the signed grid configuration -- re-read the page "
                           "and try again)" if r.status_code == 403 else ""))
                last = PortalError(f"{method} {url} -> HTTP {r.status_code}")
            time.sleep(min(2 ** attempt, 8))
        raise PortalError(f"{method} {url} failed after {self.retries} "
                          f"attempts: {last}")

    def get_html(self, path: str) -> str:
        r = self._send("GET", urljoin(self.base + "/", path.lstrip("/")))
        html = r.text
        try:
            self.token = parse.verification_token(html)
        except parse.PageShapeError:
            pass          # some pages carry no token; keep the previous one
        return html

    def grid(self, cfg: parse.GridConfig, page: int = 1,
             page_size: int = 100, search: str = "",
             sort: str | None = None) -> dict[str, Any]:
        if not self.token:
            raise PortalError("no verification token yet -- fetch a page first")
        url = urljoin(self.base + "/", (cfg.get_url or "").lstrip("/"))
        r = self._send("POST", url, json=cfg.body(page=page, page_size=page_size,
                                                  search=search, sort=sort),
                       headers={"__RequestVerificationToken": self.token,
                                "X-Requested-With": "XMLHttpRequest",
                                "Accept": "application/json"})
        try:
            return r.json()
        except ValueError as exc:
            raise PortalError(
                f"{url} did not answer with JSON -- got "
                f"{r.headers.get('Content-Type', '?')}; this usually means a "
                f"proxy or sign-in page was returned instead") from exc

    # ------------------------------------------------------------ listing

    def listing_config(self) -> parse.GridConfig:
        return parse.listing_config(self.get_html(LISTING_PATH))

    def sweep(self, cfg: parse.GridConfig | None = None,
              page_size: int = PAGE_SIZE,
              on_page: Callable[[int, int, bool], None] | None = None,
              ) -> Iterator[dict[str, Any]]:
        """Every company in the supplier list, once.

        Stops on MoreRecords, and also when a page brings nothing new: asking
        past the end serves the last page again rather than an empty one, and
        `ItemCount` is capped at 5000 whatever the real number is, so neither
        can be used to decide when to stop.
        """
        cfg = cfg or self.listing_config()
        seen: set[str] = set()
        page = 1
        while True:
            payload = self.grid(cfg, page=page, page_size=page_size)
            rows = parse.records(payload)
            fresh = 0
            for rec in rows:
                row = parse.company(rec)
                if row is None or row["id"] in seen:
                    continue
                seen.add(row["id"])
                fresh += 1
                yield row
            if on_page:
                on_page(page, fresh, parse.more(payload))
            if not rows or fresh == 0 or not parse.more(payload):
                return
            page += 1

    def search(self, term: str, cfg: parse.GridConfig | None = None,
               limit: int = 20) -> list[dict[str, Any]]:
        """Look one company up by CR number or name.

        The portal's search box matches `icv_companynamecrsearch`, which is
        the CR number and the name concatenated, so a CR number finds the one
        company and a name finds anything containing it.
        """
        cfg = cfg or self.listing_config()
        payload = self.grid(cfg, page=1, page_size=limit, search=term)
        out = []
        for rec in parse.records(payload):
            row = parse.company(rec)
            if row:
                out.append(row)
        return out

    # ------------------------------------------------------------- detail

    def detail_page(self, company_id: str, status_code: int | None = None
                    ) -> str:
        path = f"{DETAIL_PATH}?id={company_id}"
        if status_code is not None:
            path += f"&status={status_code}"
        return self.get_html(path)

    def history(self, company_id: str, status_code: int | None = None,
                html: str | None = None,
                page_size: int = HISTORY_PAGE_SIZE) -> list[dict[str, Any]]:
        """A company's published score history, oldest first.

        Needs that company's own page first: the signed configuration carries
        the company's identity, and passing someone else's entityId beside it
        is ignored -- the server answers for whoever signed the configuration.
        This was measured, not assumed.
        """
        html = html if html is not None else self.detail_page(company_id,
                                                              status_code)
        cfg = parse.history_config(html)
        payload = self.grid(cfg, page=1, page_size=page_size,
                            sort="icv_posteddate ASC")
        rows = [parse.history(r) for r in parse.records(payload)]
        rows = [r for r in rows if r]
        rows.sort(key=lambda r: r["posted_at"])
        return rows

    def goods_and_services(self, company_id: str, html: str,
                           page_size: int = 200) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cfg in parse.goods_services_configs(html):
            payload = self.grid(cfg, page=1, page_size=page_size)
            for rec in parse.records(payload):
                row = parse.goods_or_service(rec)
                if row:
                    out.append(row)
        return out


def slim(payload: dict[str, Any]) -> dict[str, Any]:
    """The part of a response worth keeping.

    Dataverse repeats a full description of every column beside every value;
    on a 1,000-record page that is megabytes of schema and kilobytes of data.
    Nothing here reads it, so it is dropped before the response is stored.
    """
    return {
        "MoreRecords": payload.get("MoreRecords"),
        "ItemCount": payload.get("ItemCount"),
        "PageNumber": payload.get("PageNumber"),
        "PageSize": payload.get("PageSize"),
        "Records": [
            {"Id": r.get("Id"), "EntityName": r.get("EntityName"),
             "Attributes": [{k: a.get(k) for k in
                             ("Name", "Type", "Value", "FormattedValue",
                              "DisplayValue")}
                            for a in (r.get("Attributes") or ())]}
            for r in (payload.get("Records") or ())
        ],
    }


def use_system_trust_store() -> bool:
    """Trust the machine's own certificate store.

    QDB's network terminates TLS at a proxy with a corporate root that is in
    Windows' store but not in certifi's, so requests otherwise fails to
    verify. Optional: absent, requests keeps its own bundle.
    """
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True
