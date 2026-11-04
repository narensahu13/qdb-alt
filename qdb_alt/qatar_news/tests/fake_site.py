"""A stand-in for the four papers, so `load` and `match` run offline.

It serves each host's robots.txt (trimmed copies of the real rules, QNA's with
its byte-order mark), the sitemap indexes and monthly sitemaps in each paper's
own shape -- Gulf Times' doubled addresses, The Peninsula's Google News tags,
QNA's paged lists -- and Gulf Times' search page. It keeps time with a stand-in
clock, so the pauses between requests can be checked without waiting for them,
logs every request with the time it was made, and can be told to fail a URL,
to cut a sitemap short, or to put a new item at the top of a QNA list.

The headlines are made up.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date.today()
Y, M = TODAY.year, TODAY.month
NOW = f"{TODAY.isoformat()}T08:00:00+03:00"

GT = "https://www.gulf-times.com"
TRIBUNE = "https://www.qatar-tribune.com"
PENINSULA = "https://thepeninsulaqatar.com"
QNA_WWW = "https://www.qna.org.qa"
QNA = "https://qna.org.qa"

GT_INDEX = f"{GT}/sitemaps/sitemap_0.xml"
GT_OLD = f"{GT}/sitemaps/2024/5/sitemap_0.xml?v=1.1"
GT_NOW = f"{GT}/sitemaps/{Y}/{M}/sitemap_0.xml?v=1.1"
GT_1970 = f"{GT}/sitemaps/1970/1/sitemap_0.xml?v=1.1"
GT_ARCHIVE_INDEX = f"{GT}/sitemaps/sitemap_article_archive.xml?v=1"
GT_ARCHIVE = f"{GT}/sitemaps/2015/5/sitemap_archive.xml"
GT_SECTIONS = f"{GT}/sitemaps/sitemap_sections.xml?v=1"
GT_AUTHORS = f"{GT}/sitemaps/sitemap_authors.xml?v=1"

TRIBUNE_INDEX = f"{TRIBUNE}/sitemaps/sitemap_0.xml"
TRIBUNE_OLD = f"{TRIBUNE}/sitemaps/2024/5/sitemap_0.xml?v=1.1"
TRIBUNE_NOW = f"{TRIBUNE}/sitemaps/{Y}/{M}/sitemap_0.xml?v=1.1"

PENINSULA_INDEX = f"{PENINSULA}/sitemap.xml"
PENINSULA_OLD = f"{PENINSULA}/sitemap_articles-2024-5.xml"
PENINSULA_CUT = f"{PENINSULA}/sitemap_articles-2024-4.xml"
PENINSULA_NOW = f"{PENINSULA}/sitemap_articles-{Y}-{M}.xml"
PENINSULA_NAV = f"{PENINSULA}/sitemap_nav.xml"

QNA_INDEX = f"{QNA_WWW}/sitemap.xml"
QNA_PAGE = f"{QNA}/CuiNormalNews?Language=en&Page={{}}&PageSize=5000"
QNA_ARABIC = f"{QNA}/CuiNormalNews?Language=ar-QA&Page=1&PageSize=5000"
QNA_PHOTOS = f"{QNA}/CuiPhotoAlbums?Language=en&Page=1&PageSize=5000"

ROBOTS = {
    "www.gulf-times.com": (FIXTURES / "robots" / "gulf_times.txt").read_text(encoding="utf-8"),
    "www.qatar-tribune.com": (FIXTURES / "robots" / "qatar_tribune.txt").read_text(encoding="utf-8"),
    "thepeninsulaqatar.com": (FIXTURES / "robots" / "peninsula.txt").read_text(encoding="utf-8"),
    "www.qna.org.qa": "\ufeff" + (FIXTURES / "robots" / "qna.txt").read_text(encoding="utf-8"),
    "qna.org.qa": "\ufeff" + (FIXTURES / "robots" / "qna.txt").read_text(encoding="utf-8"),
}

SEARCH = {  # Gulf Times search: query -> page
    "Sunrise Dates Factory": (FIXTURES / "gt_search.html").read_text(encoding="utf-8"),
}
NO_RESULTS = (FIXTURES / "gt_search_none.html").read_text(encoding="utf-8")


def index(locs: list[tuple[str, str]]) -> str:
    items = "".join(f"\t\t<sitemap>\n\t\t<loc>{loc}</loc>\n\t\t<lastmod>{mod}</lastmod>\n\t</sitemap>\n"
                    for loc, mod in locs)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}</sitemapindex>\n')


def urlset(items: list[tuple[str, str]], prefix: str = "") -> str:
    """Gulf Times / Tribune shape: <loc> (doubled by Gulf Times) and <lastmod>, no title."""
    body = "".join(f"    \t<url>\n\t\t\t\t<loc>{prefix}{loc}</loc>\n\t\t\t\t<lastmod>{mod}</lastmod>\n\t</url>\n"
                   for loc, mod in items)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}    </urlset>')


def qna_page(items: list[tuple[str, str]]) -> str:
    """QNA's shape: one line, <sitemap> as the root of a list of <url>."""
    body = "".join(f"<url><loc>{loc}</loc><lastmod>{mod}</lastmod><changefreq>daily</changefreq>"
                   f"<priority>0.5</priority></url>" for loc, mod in items)
    return f'<sitemap xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemap>'


def qna_item(slug: str, day: str) -> tuple[str, str]:
    y, m, d = day.split("-")
    return f"{QNA}/en/News-Area/News/{int(y)}-{int(m)}/{int(d)}/{slug}", f"{day} 09:30:00"


class Response:
    def __init__(self, status: int, body: str | bytes, url: str = ""):
        self.status_code, self.encoding, self.url = status, "utf-8", url
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.text = body if isinstance(body, str) else body.decode("utf-8", "replace")


class FakeTimer:
    """A clock that only moves when someone sleeps."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


class FakeSite:
    def __init__(self, timer: FakeTimer | None = None):
        self.timer = timer or FakeTimer()
        self.calls: Counter[str] = Counter()
        self.log: list[tuple[str, float]] = []
        self.headers: list[dict] = []
        self.failing: set[str] = set()      # answer 503
        self.cut: set[str] = set()          # serve cut short (no closing tag)
        self.robots = dict(ROBOTS)
        self.qna_new: list[tuple[str, str]] = []
        self.qna = [  # newest first, as QNA lists them; 2 items a page here instead of 5,000
            qna_item("sunrise-dates-factory-signs-export-deal", TODAY.isoformat()),
            qna_item("delta-variant-cases-fall-in-the-region", "2024-03-02"),
            qna_item("hia-welcomes-record-passengers", "2024-03-01"),
            qna_item("qatar-chamber-hosts-trade-delegation", "2024-02-20"),
            qna_item("national-day-celebrations-announced", "2023-12-01"),
        ]
        self.pages = {
            GT_INDEX: index([(f"{GT}//sitemaps/0/0/sitemap_0.xml?v=1.1", "2026-09-02T19:20:43+03:00"),
                             (f"{GT}//sitemaps/1970/1/sitemap_0.xml?v=1.1", NOW),
                             (f"{GT}//sitemaps/2024/5/sitemap_0.xml?v=1.1", "2024-06-02T10:00:00+03:00"),
                             (f"{GT}//sitemaps/{Y}/{M}/sitemap_0.xml?v=1.1", NOW),
                             (f"{GT}//sitemaps/sitemap_article_archive.xml?v=1", NOW),
                             (f"{GT}//sitemaps/sitemap_authors.xml?v=1", NOW),
                             (f"{GT}//sitemaps/sitemap_sections.xml?v=1", NOW)]),
            GT_ARCHIVE_INDEX: index([(GT_ARCHIVE, "2015-05-31T00:00:00+03:00")]),
            GT_ARCHIVE: urlset([(f"{GT}/story/437001/sunrise-dates-factory-wins-quality-award",
                                 "2015-05-10T09:00:00+03:00"),
                                (f"{GT}/story/437002/boy-rescued-from-the-rubble", "2015-05-11T09:00:00+03:00")]),
            GT_OLD: urlset([(f"{GT}/article/700001/business/sunrise-dates-factory-opens-new-production-line",
                             "2024-05-12T00:00:00+03:00"),
                            (f"{GT}/article/700002/business/delta-corporation-wins-port-contract",
                             "2024-05-13T00:00:00+03:00"),
                            (f"{GT}/article/700003/qatar/new-doha-bus-routes-announced",
                             "2024-05-14T00:00:00+03:00")], prefix=f"{GT}/"),
            GT_NOW: urlset([(f"{GT}/article/737001/qatar/pearl-coast-law-firm-hosts-legal-forum",
                             f"{Y}-{M:02d}-01T00:00:00+03:00")], prefix=f"{GT}/"),
            GT_SECTIONS: urlset([(f"{GT}/qatar", "")], prefix=f"{GT}/"),
            GT_AUTHORS: urlset([(f"{GT}/author/1/someone", "")], prefix=f"{GT}/"),

            TRIBUNE_INDEX: index([(TRIBUNE_OLD, "2024-06-01T10:00:00+03:00"), (TRIBUNE_NOW, NOW),
                                  (f"{TRIBUNE}/sitemaps/sitemap_sections.xml?v=1", NOW)]),
            TRIBUNE_OLD: urlset([(f"{TRIBUNE}/article/200001/business/Sunrise-Dates-Factory-opens-new-production-line",
                                  "2024-05-12T04:39:00+03:00")]),
            TRIBUNE_NOW: urlset([(f"{TRIBUNE}/article/256001/nation/hia-welcomes-record-passengers",
                                  f"{Y}-{M:02d}-01T00:00:00+03:00")]),

            PENINSULA_INDEX: index([(PENINSULA_NAV, TODAY.isoformat()), (PENINSULA_NOW, TODAY.isoformat()),
                                    (PENINSULA_OLD, TODAY.isoformat()), (PENINSULA_CUT, TODAY.isoformat())]),
            PENINSULA_OLD: (FIXTURES / "peninsula_articles.xml").read_text(encoding="utf-8"),
            PENINSULA_CUT: urlset([(f"{PENINSULA}/article/30/04/2024/a-whole-april-headline", "2024-04-30")]),
            PENINSULA_NOW: urlset([(f"{PENINSULA}/article/01/{M:02d}/{Y}/ministry-opens-new-service-centre",
                                    TODAY.isoformat())]),
            PENINSULA_NAV: urlset([(f"{PENINSULA}/qatar", "")]),
        }

    # ---- QNA's paged lists ---------------------------------------------------

    def _qna(self, url: str) -> str | None:
        items = self.qna_new + self.qna
        pages = [items[i:i + 2] for i in range(0, len(items), 2)]
        if url == QNA_INDEX:
            return index([(QNA_PAGE.format(k + 1).replace("&", "&amp;"), p[0][1]) for k, p in enumerate(pages)]
                         + [(QNA_ARABIC.replace("&", "&amp;"), NOW), (QNA_PHOTOS.replace("&", "&amp;"), NOW)])
        for k, p in enumerate(pages):
            if url == QNA_PAGE.format(k + 1):
                return qna_page(p)
        if url == QNA_PHOTOS:
            return qna_page([qna_item("photo-album-amir-receives-credentials", "2024-01-05")])
        if url == QNA_ARABIC:
            return qna_page([qna_item("arabic-item", "2024-01-05")])
        return None

    # ---- the session ---------------------------------------------------------

    def get(self, url, headers=None, timeout=None):
        self.calls[url] += 1
        self.log.append((url, self.timer.monotonic()))
        self.headers.append(headers)
        parts = urlsplit(url)
        if url in self.failing:
            return Response(503, "Service Unavailable", url)
        if parts.path == "/robots.txt":
            text = self.robots.get(parts.netloc)
            return Response(200, text, url) if text is not None else Response(404, "Not Found", url)
        if parts.netloc == "www.gulf-times.com" and parts.path == "/search":
            q = parse_qs(parts.query)
            assert q.get("match") == ["exact"], url
            return Response(200, SEARCH.get(q["query"][0], NO_RESULTS), url)
        body = self.pages.get(url) or self._qna(url)
        if body is None:
            return Response(404, "<html>Not Found</html>", url)
        if url in self.cut:
            body = body[: len(body) * 2 // 3]
        return Response(200, body, url)
