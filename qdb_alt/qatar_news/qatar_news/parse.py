"""From the papers' sitemaps to one record per article, and from a Gulf Times
search page to a count of articles.

What each paper publishes for search engines, and what is kept of it:

    Gulf Times      /sitemaps/sitemap_0.xml: one sitemap per month since
                    Nov 2022 (/sitemaps/YYYY/M/sitemap_0.xml), plus
                    sitemap_article_archive.xml, an index of one archive
                    sitemap per month from 2011 to Nov 2022. Article
                    addresses come doubled
                    ("https://www.gulf-times.com/https://www.gulf-times.com/article/...");
                    the last "https://" is the real one. No titles: the title
                    is read from the address ("qdb-joins-building-materials-...").
    Qatar Tribune   the same system as Gulf Times: monthly sitemaps since 2022
                    and an archive index from 2016.
    The Peninsula   /sitemap.xml: one sitemap per month
                    (sitemap_articles-YYYY-M.xml) with Google News tags --
                    news:title (the real headline, in its own capitals) and
                    news:publication_date. For articles moved over from the
                    old site the publication date is the day of the move
                    (2021), so the date in the address
                    (/article/DD/MM/YYYY/...) is used when there is one.
    QNA             /sitemap.xml: paged lists of 5,000 items per section and
                    language, newest first ("CuiNormalNews?Language=en&Page=3").
                    Only the English lists are read; titles from the address.

Author, section, navigation, video and PDF sitemaps are skipped: they are not
articles.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

S = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NEWS = "{http://www.google.com/schemas/sitemap-news/0.9}"


@dataclass(frozen=True)
class Paper:
    key: str             # what --papers takes
    name: str            # what the article table and the features say
    index: str           # the sitemap index robots.txt points to
    real_lastmod: bool   # the index's <lastmod> says when each sitemap last changed


PAPERS = {p.key: p for p in [
    Paper("gulf_times", "Gulf Times", "https://www.gulf-times.com/sitemaps/sitemap_0.xml", True),
    Paper("qatar_tribune", "Qatar Tribune", "https://www.qatar-tribune.com/sitemaps/sitemap_0.xml", True),
    Paper("qna", "QNA", "https://www.qna.org.qa/sitemap.xml", False),
    # last: at its 10-second Crawl-delay it is most of the first load's hour
    Paper("peninsula", "The Peninsula", "https://thepeninsulaqatar.com/sitemap.xml", False),
]}

MONTHLY = re.compile(r"/sitemaps/(\d{4})/(\d{1,2})/sitemap_(?:\d+|archive)\.xml$")    # Gulf Times, Tribune
ARCHIVE_INDEX = re.compile(r"/sitemaps/sitemap_article_archive\.xml$")
PENINSULA_MONTH = re.compile(r"/sitemap_articles-(\d{4})-(\d{1,2})\.xml$")
PENINSULA_DATE = re.compile(r"/article/(\d{2})/(\d{2})/(\d{4})/")
CLOSED = re.compile(rb"</(?:[\w-]+:)?(?:urlset|sitemapindex|sitemap)>\s*$")
# Control characters XML 1.0 does not allow; one stray backspace in a headline (The Peninsula,
# July 2015) makes a whole month's sitemap unreadable, so they are blanked before parsing.
ILLEGAL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class Incomplete(ValueError):
    """The sitemap did not arrive whole (cut off, or a page that is not a sitemap)."""


@dataclass(frozen=True)
class Child:
    url: str
    lastmod: str
    kind: str            # month | index | feed
    period: str | None   # 'YYYY-MM' for a month
    feed: str | None     # QNA: the list the page belongs to ('CuiNormalNews')
    page: int | None     # QNA: its page number


# ---- sitemaps ---------------------------------------------------------------

def read_sitemap(data: bytes) -> tuple[str, ET.Element]:
    """('index' | 'urls', root). Only a whole document is accepted: it must parse
    and end with its closing tag, or Incomplete is raised and nothing of it is kept."""
    if not CLOSED.search(data):
        raise Incomplete("the sitemap does not end with its closing tag (cut off?)")
    try:
        root = ET.fromstring(ILLEGAL.sub(b" ", data))
    except ET.ParseError as e:
        raise Incomplete(f"the sitemap is not well-formed XML: {e}") from e
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "sitemapindex":
        return "index", root
    if tag in ("urlset", "sitemap"):          # QNA's pages have <sitemap> as the root of a list of <url>
        return "urls", root
    raise Incomplete(f"not a sitemap: <{tag}>")


def _clean_child(loc: str) -> str:
    """'https://www.gulf-times.com//sitemaps/2026/10/sitemap_0.xml?v=1.1' -> one slash."""
    p = urlsplit(loc.strip())
    return urlunsplit((p.scheme, p.netloc, re.sub(r"/{2,}", "/", p.path), p.query, ""))


def children(paper: Paper, root: ET.Element) -> list[Child]:
    """The sitemaps of an index that hold articles, in index order; the rest is skipped."""
    out, seen = [], set()
    for sm in root.iter(f"{S}sitemap"):
        url = _clean_child(sm.findtext(f"{S}loc") or "")
        lastmod = (sm.findtext(f"{S}lastmod") or "").strip()
        child = classify(paper, url, lastmod)
        if child and child.url not in seen:
            seen.add(child.url)
            out.append(child)
    return out


def classify(paper: Paper, url: str, lastmod: str = "") -> Child | None:
    path, query = urlsplit(url).path, urlsplit(url).query
    if paper.key in ("gulf_times", "qatar_tribune"):
        m = MONTHLY.search(path)
        if m:
            return Child(url, lastmod, "month", f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", None, None)
        if ARCHIVE_INDEX.search(path):
            return Child(url, lastmod, "index", None, None, None)
    elif paper.key == "peninsula":
        m = PENINSULA_MONTH.search(path)
        if m:
            return Child(url, lastmod, "month", f"{int(m.group(1)):04d}-{int(m.group(2)):02d}", None, None)
    elif paper.key == "qna":
        q = parse_qs(query)
        if q.get("Language") == ["en"] and q.get("Page", [""])[0].isdigit():
            return Child(url, lastmod, "feed", None, path.strip("/"), int(q["Page"][0]))
    return None


def article_url(loc: str) -> str:
    """The last 'https://' in a doubled Gulf Times address is the real one."""
    loc = loc.strip()
    return "https://" + loc.rsplit("https://", 1)[-1] if "https://" in loc else loc


def title_from_url(url: str) -> str:
    """'.../article/734556/qatar/qdb-joins-building-materials-exhibition' -> 'qdb joins building materials exhibition'"""
    slug = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    return " ".join(re.sub(r"[-_+]+", " ", slug).split())


def article_date(paper: Paper, url: str, published: str, lastmod: str) -> str | None:
    if paper.key == "peninsula":
        m = PENINSULA_DATE.search(url)
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    day = (published or lastmod or "").strip()[:10]
    # '1970-01-01' (an empty date read as the epoch) on a few Gulf Times articles is not a date
    return day if re.fullmatch(r"(?:199\d|20\d\d)-\d{2}-\d{2}", day) else None


def articles(paper: Paper, root: ET.Element) -> list[dict[str, str | None]]:
    """One record per <url>: paper, url, date, title (news:title when the paper gives one)."""
    out = []
    for u in root.iter(f"{S}url"):
        url = article_url(u.findtext(f"{S}loc") or "")
        if not url.startswith("https://"):
            continue
        news_title = " ".join(html.unescape(u.findtext(f".//{NEWS}title") or "").split())
        out.append({"paper": paper.name, "url": url,
                    "date": article_date(paper, url, u.findtext(f".//{NEWS}publication_date") or "",
                                         u.findtext(f"{S}lastmod") or ""),
                    "title": news_title or title_from_url(url)})
    return out


# ---- Gulf Times search ------------------------------------------------------

SEARCH_COUNT = re.compile(r'class="search-count">\s*\((\d[\d,]*) articles?\)')
SEARCH_TITLE = re.compile(r'<h2 class="article-title">\s*<a\b[^>]*\btitle="([^"]*)"')


def gt_search(page: str) -> tuple[int, list[str]]:
    """(articles found, titles on the first page). A page without the count is
    not a result page (a block page, an error page): ValueError, never 0."""
    m = SEARCH_COUNT.search(page)
    if not m:
        raise ValueError("no result count on the Gulf Times search page")
    titles = [" ".join(html.unescape(t).split()) for t in SEARCH_TITLE.findall(page)]
    return int(m.group(1).replace(",", "")), list(dict.fromkeys(t for t in titles if t))
