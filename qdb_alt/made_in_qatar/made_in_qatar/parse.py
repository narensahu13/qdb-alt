"""From the site's sitemap and factory pages to one record per factory.

robots.txt on madeinqatar.qa disallows /wp-json/ (the WordPress API), so the
register is read the way a search engine reads it: the factories sitemap
lists every factory page with its last-modified date, and each page is plain
HTML. On a factory page the fields come as labelled lines:

    Qatar Detergent Company (QDC)          <- page title
    Address : Al Wakalat Street, ...
    Sector : Manufacture of chemicals and chemical products
    <the company's own description>        <- founding year is read from here
    Our Website                            <- a link to the company site
    Our Product                            <- links to its product pages
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
FOUNDED = re.compile(r"(?:establish\w*|founded|since|incorporated|inception)[^.]{0,30}?\b((?:19|20)\d\d)\b", re.I)
END_OF_DESCRIPTION = {"Our Website", "Our Product", "Our Products", "Media Gallery", "Important Links"}


def sitemap(xml_text: str) -> list[tuple[str, str]]:
    """(url, lastmod) for every factory page; the archive page /factories/ itself is skipped."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    out = []
    for u in root.iter(f"{SITEMAP_NS}url"):
        loc = (u.findtext(f"{SITEMAP_NS}loc") or "").strip()
        if re.search(r"/factories/[^/]+/?$", loc):
            out.append((loc, (u.findtext(f"{SITEMAP_NS}lastmod") or "").strip()))
    return out


def _lines(page: str) -> list[str]:
    body = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", page, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    return [" ".join(l.split()) for l in text.split("\n") if l.strip()]


def _after_label(lines: list[str], label: str) -> str | None:
    """'Address' then ': Al Wakalat Street' (or 'Sector', ':', 'Manufacture of ...')."""
    for i, line in enumerate(lines[:-1]):
        if line == label:
            value = lines[i + 1].lstrip(":").strip() or (lines[i + 2] if i + 2 < len(lines) else "")
            return value.strip() or None
    return None


def founding_year(lines: list[str]) -> int | None:
    """'... since its establishment in 2015' -> 2015. A description written as a timeline, one
    step per line ('1976' / 'First machines ordered' / '1977' / 'Fully founded' ... '2013' /
    'Moved to the New Industrial Area') -> its first year: there the phrase match would land on
    whichever later step says 'established'. Years inside sentences (ISO 9001:2015, Vision 2030)
    do not make a timeline, nor does a list of news items (newest first)."""
    steps = [int(m.group(1)) for l in lines if (m := re.match(r"((?:19|20)\d\d)\b", l))
             and int(m.group(1)) <= date.today().year]
    if len(steps) >= 3 and steps == sorted(steps):
        return steps[0]
    m = FOUNDED.search(" ".join(lines))
    return int(m.group(1)) if m else None


def parse_factory(page: str, url: str) -> dict[str, Any]:
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    name = html.unescape(title.group(1)).split("|")[0].strip() if title else None
    lines = _lines(page)
    sector = _after_label(lines, "Sector")
    # the description: from the line after the sector to the first link block
    start = next((i + 1 for i, l in enumerate(lines) if sector and l == sector), None)
    description = []
    if start is not None:
        for line in lines[start:]:
            if line in END_OF_DESCRIPTION:
                break
            description.append(line)
    text = " ".join(description)
    founded = founding_year(description)
    site = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>\s*Our Website\s*</a>', page)
    products = sorted(set(re.findall(r'href="(https://madeinqatar\.qa/products/[^"/]+/?)"', page)))
    return {
        "url": url,
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
        "name": name,
        "address": _after_label(lines, "Address"),
        "sector": sector,
        "founded_year": founded,
        "website": site.group(1) if site else None,
        "products": len(products),
        "description_chars": len(text),
    }
