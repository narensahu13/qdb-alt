"""A stand-in for qicdrc.gov.qa, so `load` runs offline.

It serves robots.txt, a sitemap built from its own list of judgment pages and
the pages themselves; it counts requests per URL, and can be told to fail a
page, to add pages, or to change a page's last-modified date between loads.
The fixture judgment page is made up, in the site's real markup; the others
here are smaller made-up pages of the same shape.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROOT = "https://www.qicdrc.gov.qa"
ROBOTS = ROOT + "/robots.txt"
SITEMAP = ROOT + "/sitemap.xml"
SUNRISE = ROOT + "/judgments/ctfic00992025-sunrise-dates-factory"
APPEAL = ROOT + "/judgments/case-no-ctad00052025-2026-qic-3"


class Response:
    def __init__(self, status: int, text: str, url: str):
        self.status_code, self.text, self.encoding, self.url = status, text, "utf-8", url


def small_page(case_no: str, citation: str, heading: str, date: str, court: str = "Court of First Instance",
               keywords: tuple[str, ...] = ()) -> str:
    """A judgment page as the site renders it, without the JSON-LD (the parser falls back to the HTML)."""
    kw = "".join(f'<div class="judgement-keywords-wrapper"><a href="/judgments?k=1" '
                 f'class="judgement-keywords-button judgement-keywords-contract">{k}</a></div>' for k in keywords)
    return f"""<html><head><title>{case_no} | Judgements | QICDRC</title>
<meta property="og:title" content="{case_no}" /></head><body>
<h2 class="page-title">{heading}</h2>
<div class="judgement-item-court-cell judgement-item-header-cell">
<div class="judgement-button-link-wrapper court-type-37"><span class="judgement-button-link">{court}</span></div></div>
<div class="judgement-body-case-info">Case No. {case_no}</div>
<div class="judgement-item-body-cell judgement-item-body-info judgement-body-citation-info">{citation}</div>
<div class="judgement-item-body-cell judgement-item-body-info judgement-body-date-info">{date}</div>
<div class="judgement-item-body-cell judgement-item-body-info judgement-body-keywords-info">{kw}</div>
</body></html>"""


APPEAL_PAGE = small_page("CTAD0005/2025", "[2026] QIC (A) 3",
                         "Blue Harbour Ice LLC v Sunrise Dates Factory WLL", "15 January 2026",
                         court="Appellate Division", keywords=("Appellate Division", "Appeal Dismissed"))


class FakeSite:
    def __init__(self):
        self.calls: Counter[str] = Counter()
        self.failing: set[str] = set()
        # the real file asks for 10 s between requests; the tests do not wait (one test puts it back)
        self.robots = (FIXTURES / "robots.txt").read_text(encoding="utf-8").replace("Crawl-delay: 10\n", "")
        self.pages = {SUNRISE: (FIXTURES / "judgment_page.html").read_text(encoding="utf-8"), APPEAL: APPEAL_PAGE}
        self.lastmod = {SUNRISE: "2026-08-07T16:15:30+03:00", APPEAL: "2026-02-01T10:00:00+03:00"}

    def add_pages(self, n: int) -> list[str]:
        """n more small judgments, each between a made-up company and one person."""
        urls = []
        for i in range(n):
            url = f"{ROOT}/judgments/ctfic{i:04d}2024"
            self.pages[url] = small_page(f"CTFIC{i:04d}/2024", f"[2024] QIC (F) {i + 1}",
                                         f"PERSON NUMBER{i} v Example Holding {i} LLC", "01 March 2024")
            self.lastmod[url] = "2024-03-01T10:00:00+03:00"
            urls.append(url)
        return urls

    @property
    def sitemap(self) -> str:
        urls = "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>"
                       f"<url><loc>{u.replace('/judgments/', '/ar/judgments/')}</loc><lastmod>{m}</lastmod></url>"
                       for u, m in self.lastmod.items())
        return ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{ROOT}/</loc></url><url><loc>{ROOT}/judgments</loc></url>{urls}</urlset>")

    def get(self, url, headers=None, timeout=None):
        self.calls[url] += 1
        if url in self.failing:
            return Response(503, "Service Unavailable", url)
        if url == ROBOTS:
            return Response(200, self.robots, url)
        if url == SITEMAP:
            return Response(200, self.sitemap, url)
        if url in self.pages:
            return Response(200, self.pages[url], url)
        return Response(404, "Not Found", url)
