"""From the sitemap and the listing pages to one record per listing.

The sitemap lists every business page (`/<slug>/i/<id>`) with its
last-modified date, beside news articles (`/<slug>/<id>/n`) and the search
pages, which are skipped. A listing page is plain HTML with schema.org
markup (`itemprop=...`):

    <h1 itemprop="name">                    the business name
    <span itemprop="department"> x 2-3      the category path, broad to narrow
    <span itemprop="ratingValue">,          only when the listing has ratings
      <span itemprop="reviewCount">
    <div class="copy">                      the business's own description
    <span itemprop="openingHours"> x 7      one per weekday, "Closed" or hours
    <p itemprop="location">                 the address
    <span itemprop="telephone">, "email"    a contact phone / e-mail
    <a itemprop="url">                      the business website
    <div class="listing-social">            links to its social media pages

Only company-level facts are kept. Whether a phone number and an e-mail
address are given is recorded as 1/0; the number and the address themselves
are not stored, since on a small business's listing they are often a
person's own.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
LISTING_URL = re.compile(r"^https://www\.doha\.directory/([^/?#]+)/i/(\d+)/?$")
FOUNDED = re.compile(r"(?:establish\w*|founded|since|incorporated|inception)[^.]{0,30}?\b((?:19|20)\d\d)\b", re.I)


def sitemap(xml_text: str) -> list[tuple[str, str]]:
    """(url, lastmod) for every listing page, in sitemap order."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    out, seen = [], set()
    for u in root.iter(f"{SITEMAP_NS}url"):
        loc = (u.findtext(f"{SITEMAP_NS}loc") or "").strip()
        if LISTING_URL.match(loc) and loc not in seen:
            seen.add(loc)
            out.append((loc, (u.findtext(f"{SITEMAP_NS}lastmod") or "").strip()))
    return out


def slug_name(url: str) -> str:
    """'https://www.doha.directory/qatar-steel-industries-factory/i/2661' -> 'qatar steel industries factory'.
    The slug is made from the name, so it stands in for it until the page has been read."""
    m = LISTING_URL.match(url)
    return " ".join(m.group(1).replace("-", " ").split()) if m else ""


def listing_id(url: str) -> int | None:
    m = LISTING_URL.match(url)
    return int(m.group(2)) if m else None


def _text(fragment: str | None) -> str:
    """Tags out, entities decoded, whitespace (including &nbsp;) collapsed."""
    if fragment is None:
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _first(pattern: str, page: str) -> str | None:
    m = re.search(pattern, page, re.S | re.I)
    return (_text(m.group(1)) or None) if m else None


def founding_year(description: str) -> int | None:
    """'Established in 1980, Qatar Steel Industries Factory ...' -> 1980; a year in the future is ignored."""
    m = FOUNDED.search(description)
    year = int(m.group(1)) if m else None
    return year if year and year <= date.today().year else None


def parse_listing(page: str, url: str) -> dict[str, Any]:
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)
    departments = [_text(d) for d in re.findall(r'<span itemprop="department">(.*?)</span>', page, re.S)]
    departments = [d for d in departments if d]
    rating = _first(r'<span itemprop="ratingValue">(.*?)</span>', page)
    reviews = _first(r'<span itemprop="reviewCount">(.*?)</span>', page)
    hours = [_text(h) for h in re.findall(r'<span itemprop="openingHours">(.*?)</span>', page, re.S)]
    description = _first(r'<div class="copy">(.*?)</div>', page) or ""
    site = re.search(r'<a\b[^>]*\bitemprop="url"[^>]*>', page)
    website = re.search(r'href="(https?://[^"]+)"', site.group(0)) if site else None
    social_block = re.search(r'<div class="listing-social"[^>]*>(.*?)</div>', page, re.S)
    social = sorted({m.group(2) for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*class="social-(\w+)"',
                                                     social_block.group(1) if social_block else "")
                     if m.group(1).strip()})
    tags = [_text(t) for t in re.findall(r'<div class="tags">\s*<a[^>]*>(.*?)</a>', page, re.S)]
    return {
        "url": url,
        "listing_id": listing_id(url),
        "slug_name": slug_name(url),
        "name": _first(r'<h1 itemprop="name">(.*?)</h1>', page),
        "category": departments[-1] if departments else None,
        "category_path": " / ".join(departments) or None,
        "rating": float(rating) if rating and re.fullmatch(r"\d+(\.\d+)?", rating) else None,
        # no rating block on the page: nobody has rated the listing
        "reviews": int(reviews) if reviews and reviews.isdigit() else 0,
        "address": _first(r'<p itemprop="location">(.*?)</p>', page),
        "has_phone": int(bool(_first(r'<span itemprop="telephone">(.*?)</span>', page))),
        "has_email": int(bool(_first(r'<span itemprop="email">(.*?)</span>', page))),
        "website": website.group(1) if website else None,
        "social": ",".join(social) or None,
        "open_days": sum(bool(h) and h.lower() != "closed" for h in hours) if hours else None,
        "founded_year": founding_year(description),
        "description_chars": len(description),
        "tags": "; ".join(t for t in tags if t) or None,
    }
