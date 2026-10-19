"""Reading the two programme pages on customs.gov.qa.

customs.gov.qa publishes no robots.txt (it answers 404), so nothing is
disallowed; still, one request at a time with a pause between them. The site
serves an incomplete certificate chain: with the Windows certificate store
(truststore) the chain verifies, so verification stays on.
"""

from __future__ import annotations

import time

import requests

BASE = "https://www.customs.gov.qa/English/Procedures/FacilitationandCompliancePrograms/Pages/"
PAGES = {"aeo": BASE + "CertifiedEconomicOperator.aspx", "green_list": BASE + "TheGreenList.aspx"}
UA = {"User-Agent": "qdb-alt/customs_trusted_traders (+https://github.com/narensahu13/qdb-alt)"}


class SiteError(RuntimeError):
    pass


def use_system_trust_store() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


class Site:
    def __init__(self, delay: float = 2.0, timeout: float = 120, session=None):
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
        if "<table" not in r.text.lower():
            raise SiteError(f"{url}: the page has no table (an error page?)")
        r.encoding = r.encoding or "utf-8"
        return r.text
