"""A stand-in for www.doha.directory, so `load` runs offline.

It serves the fixture sitemap (gzipped, as the real one is) and the listing
pages, counts requests per URL, and can be told to fail a page, to remove a
page, or to change a page's last-modified date between loads.
"""

from __future__ import annotations

import gzip
from collections import Counter
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SITEMAP = "https://www.doha.directory/sitemap.xml.gz"
SUNRISE = "https://www.doha.directory/sunrise-dates-factory/i/2201"
BLUE = "https://www.doha.directory/blue-harbour-ice/i/2202"
PEARL = "https://www.doha.directory/------pearl-coast-law-firm/i/2203"
WAKRA = "https://www.doha.directory/sunrise-date-factory-wakra/i/2204"

# A listing with nothing but a name, a category and an address: no ratings, no hours, no links.
BLUE_PAGE = """<html><body><section class="product-detail">
<h1 itemprop="name">Blue Harbour Ice</h1>
<p><span itemprop="department">Services</span>/ <span itemprop="department">Ice Suppliers</span>/</p>
<div class="copy">Ice for fishing boats.</div>
<p itemprop="location">Mesaieed, Qatar</p>
<div class="listing-social" style="text-align-last: center;">
 </div>
</section></body></html>"""

# The title in Arabic only; the web address keeps the English name.
PEARL_PAGE = """<html><body><section class="product-detail">
<h1 itemprop="name">&#1605;&#1603;&#1578;&#1576; &#1575;&#1604;&#1604;&#1572;&#1604;&#1572;</h1>
<p><span itemprop="department">Services</span>/ <span itemprop="department">Law Firms</span>/</p>
<span itemprop="telephone">44000000</span>
</section></body></html>"""

# A second listing of the same business, under the same name.
WAKRA_PAGE = """<html><body><section class="product-detail">
<h1 itemprop="name">Sunrise Dates Factory</h1>
<p><span itemprop="department">Services</span>/ <span itemprop="department">Food Manufacturing</span>/</p>
<p itemprop="location">Al Wakrah, Qatar</p>
</section></body></html>"""


class Response:
    def __init__(self, status: int, body: str | bytes):
        self.status_code, self.encoding = status, "utf-8"
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.text = body if isinstance(body, str) else ""


class FakeSite:
    def __init__(self):
        self.calls: Counter[str] = Counter()
        self.failing: set[str] = set()
        self.sitemap = (FIXTURES / "sitemap.xml").read_text(encoding="utf-8")
        self.pages = {SUNRISE: (FIXTURES / "listing_page.html").read_text(encoding="utf-8"),
                      BLUE: BLUE_PAGE, PEARL: PEARL_PAGE, WAKRA: WAKRA_PAGE}

    def get(self, url, headers=None, timeout=None):
        self.calls[url] += 1
        if url in self.failing:
            return Response(503, "Service Unavailable")
        if url == SITEMAP:
            return Response(200, gzip.compress(self.sitemap.encode("utf-8")))
        if url in self.pages:
            return Response(200, self.pages[url])
        return Response(404, "Not Found")
