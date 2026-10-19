"""From the directory's HTML to records.

A search result page lists matching companies as plain links, nothing else:

    <a class="alphbet-style" href="https://qataronlinedirectory.com/company-details/11809/Qatar-Steel-Industries-Factory-WLL">
        Qatar Steel Industries Factory WLL
    </a>

and always says what was searched for ("You searched for ... which
contains"); a page without that sentence is not a result page (an error or
block page) and is refused rather than read as "no results".

A listing page has the company name in the <h1>, a "Company Details" box with
the P O Box, the city and a phone number, then the categories the company is
listed under and, for some, the brands it carries. The page template has a
website button, but on none of the listings read so far (October 2026) is it
filled in, and listings show no e-mail address, so neither is read.

Only company-level facts are kept, and contact details only as present / not
present: the phone number and the P O Box number are never stored. Parts of
the page commented out in the HTML are ignored.
"""

from __future__ import annotations

import html
import re
from typing import Any

LISTING = re.compile(r'<a\b[^>]*href="(?:https?://(?:www\.)?qataronlinedirectory\.com)?'
                     r'/company-details/(\d+)/([^"/?#]+)"[^>]*>(.*?)</a>', re.S | re.I)
PAGE_LINK = re.compile(r'href="[^"]*/search\?company=[^"]*?[?&](?:amp;)?page=(\d+)', re.I)
H1 = re.compile(r'<h1[^>]*class="[^"]*h1-heading-style[^"]*"[^>]*>(.*?)</h1>', re.S | re.I)
PO_BOX = re.compile(r"P\.?\s*O\.?\s*Box\s*:?\s*\d+", re.I)
CITY = re.compile(r">\s*([A-Za-z][A-Za-z .'-]{1,40}?)\s*,\s*Qatar\s*<", re.I)
PHONE = re.compile(r"\+?\d[\d ()/-]{5,}\d")
CATEGORY = re.compile(r'href="[^"]*/categories/[^"]+"[^>]*>(.*?)</a>', re.S | re.I)
BRAND = re.compile(r'href="[^"]*/branddetails/\d+/[^"]*"[^>]*>(.*?)</a>', re.S | re.I)


class PageError(ValueError):
    """The response is not the page we asked for (an error or block page)."""


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _uncomment(page: str) -> str:
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    return re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)


def parse_search(page: str) -> tuple[list[dict[str, Any]], int]:
    """(listings, number of result pages). A listing is {listing_id, slug, name, url}."""
    if not re.search(r"You searched for", page, re.I):
        raise PageError("not a search result page")
    page = _uncomment(page)
    out, seen = [], set()
    for lid, slug, name in LISTING.findall(page):
        if lid in seen:
            continue
        seen.add(lid)
        slug = html.unescape(slug)
        out.append({"listing_id": int(lid), "slug": slug, "name": _text(name) or slug.replace("-", " "),
                    "url": f"https://qataronlinedirectory.com/company-details/{lid}/{slug}"})
    pages = max([1] + [int(n) for n in PAGE_LINK.findall(page)])
    return out, pages


def parse_company(page: str) -> dict[str, Any]:
    """The company-level fields of one listing page."""
    page = _uncomment(page)
    h1 = H1.search(page)
    if not h1:
        raise PageError("not a company page")
    body = page[h1.start():]
    end = re.search(r"<footer\b", body, re.I)
    body = body[:end.start()] if end else body
    # the "Company Details" box: from the header to the category list
    box_end = re.search(r"listed under", body, re.I)
    box = body[:box_end.start()] if box_end else body
    box_text = _text(PO_BOX.sub(" ", box))
    city = CITY.search(box)
    categories = list(dict.fromkeys(t for t in (_text(c) for c in CATEGORY.findall(body)) if t))
    brands = list(dict.fromkeys(t for t in (_text(b) for b in BRAND.findall(body)) if t))
    return {
        "name": _text(h1.group(1)) or None,
        "city": city.group(1).strip().title() if city else None,
        "has_po_box": int(PO_BOX.search(box) is not None),
        "has_phone": int(PHONE.search(box_text) is not None),
        "categories": categories,
        "brands": brands,
    }
