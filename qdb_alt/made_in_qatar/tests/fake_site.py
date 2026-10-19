"""A stand-in for madeinqatar.qa, so `load` runs offline.

It serves the fixture sitemap and pages, counts requests per URL, and can be
told to fail a page or to change a page's last-modified date between loads.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SITEMAP = "https://madeinqatar.qa/factories-sitemap.xml"
SUNRISE = "https://madeinqatar.qa/factories/sunrise-dates-factory/"
BLUE = "https://madeinqatar.qa/factories/blue-harbour-ice/"

BLUE_PAGE = """<html><head><title>Blue Harbour Ice | Made in Qatar Gate</title></head><body>
<span><b>Address</b>: Mesaieed, Qatar</span>
<span><b>Sector</b>: Manufacture of beverages</span>
<p>Blue Harbour Ice supplies ice to fishing boats.</p>
<p>Media Gallery</p></body></html>"""


class Response:
    def __init__(self, status: int, text: str):
        self.status_code, self.text, self.encoding = status, text, "utf-8"


class FakeSite:
    def __init__(self):
        self.calls: Counter[str] = Counter()
        self.failing: set[str] = set()
        self.sitemap = (FIXTURES / "factories_sitemap.xml").read_text(encoding="utf-8")
        self.pages = {SUNRISE: (FIXTURES / "factory_page.html").read_text(encoding="utf-8"), BLUE: BLUE_PAGE}

    def get(self, url, headers=None, timeout=None):
        self.calls[url] += 1
        if url in self.failing:
            return Response(503, "Service Unavailable")
        if url == SITEMAP:
            return Response(200, self.sitemap)
        if url in self.pages:
            return Response(200, self.pages[url])
        return Response(404, "Not Found")
