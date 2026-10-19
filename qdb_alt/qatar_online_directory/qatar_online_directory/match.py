"""Joining a customer book to the directory.

The directory has no CR numbers, so each customer is searched by name and the
result list is filtered with the strict rule in namematch.py (`same_company`):
every distinctive word of the customer's name in the listing, the brand word
first and no other brand in front of it. The site's search is a plain
"name contains" search, so a short or generic name can bring back several
companies; only the listings that pass the rule are kept, and of those only
the ones within 5 points of the closest name -- a second branch of the same
company stays, a looser neighbour does not.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .namematch import same_company, search_name, similarity

NEAR_BEST = 5
HEADERS = {  # accepted column names, as in the customer books used by icv and monaqasat
    "customer_id": {"customer_id", "id", "client_id"},
    "name": {"name", "company_name", "company", "customer_name"},
    "cr_number": {"cr_number", "cr", "cr_no", "commercial_registration"},
    # optional: how to search when the registered name is not the trading name
    # (QATAR GERMAN PIPES -> Qatar German Polymer)
    "search_name": {"search_name"},
}


def load_customers(path: str | Path) -> list[dict[str, str]]:
    """customer_id, name, cr_number from a CSV; a missing customer_id becomes the row number."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = {field: next((h for h in rows[0] if h and h.strip().lower() in names), None)
            for field, names in HEADERS.items()}
    if not cols["name"]:
        raise ValueError(f"{Path(path).name}: no name column (accepted: {sorted(HEADERS['name'])})")
    cell = lambda r, field: (r.get(cols[field]) or "").strip() if cols[field] else ""
    out = []
    for i, r in enumerate(rows, start=1):
        if cell(r, "name"):
            out.append({"customer_id": cell(r, "customer_id") or str(i), "name": cell(r, "name"),
                        "cr_number": cell(r, "cr_number"), "search_name": cell(r, "search_name")})
    return out


def query_for(customer: dict[str, str]) -> str:
    """What is typed into the site's company-name search."""
    return customer.get("search_name") or search_name(customer["name"])


def pick(query: str, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The listings that are this company, closest name first."""
    hits = [(similarity(query, l["name"]), l) for l in listings if same_company(query, l["name"])]
    if not hits:
        return []
    best = max(s for s, _ in hits)
    return [{**l, "score": round(s, 1)} for s, l in sorted(hits, key=lambda h: -h[0]) if s >= best - NEAR_BEST]


def match_one(customer: dict[str, str], search: dict[str, Any] | None) -> dict[str, Any]:
    """The customer's match. `search` is the stored search for its query; with no
    successful search the status is 'no search' and every feature stays empty."""
    q = query_for(customer)
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "query": q, "status": "no search",
           "listings": [], "matched_name": None, "score": None}
    if search is None or search["status"] != "ok":
        return row
    hits = pick(q, search["listings"])
    row["status"] = "found" if hits else "not found"
    if hits:
        row.update(listings=hits, matched_name=hits[0]["name"], score=hits[0]["score"])
    return row


def features(row: dict[str, Any], companies: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Model-ready columns for one customer.

    Empty means unknown, not zero: a customer whose search failed has every
    column empty; one that was searched and is not listed has qod_listed = 0
    and the rest empty; a listed one whose listing page could not be read has
    qod_listed = 1 and the page's columns empty. With several listings the
    flags and counts cover all of them, the city and main category come from
    the closest name."""
    out = {"customer_id": row["customer_id"], "customer_name": row["customer_name"], "cr_number": row["cr_number"],
           "qod_listed": None, "qod_listings": None, "qod_city": None, "qod_main_category": None,
           "qod_categories": None, "qod_category_list": None, "qod_brands": None,
           "qod_has_po_box": None, "qod_has_phone": None,
           "matched_name": row["matched_name"], "match_score": row["score"], "listing_url": None}
    if row["status"] == "no search":
        return out
    out["qod_listed"] = int(row["status"] == "found")
    if row["status"] != "found":
        return out
    out["qod_listings"] = len(row["listings"])
    out["listing_url"] = " ".join(l["url"] for l in row["listings"])
    pages = [c for l in row["listings"] if (c := companies.get(l["listing_id"])) and c["status"] == "ok"]
    if not pages:
        return out
    categories = list(dict.fromkeys(c for p in pages for c in p["categories"]))
    brands = list(dict.fromkeys(b for p in pages for b in p["brands"]))
    out.update(
        qod_city=next((p["city"] for p in pages if p["city"]), None),
        qod_main_category=next((p["categories"][0] for p in pages if p["categories"]), None),
        qod_categories=len(categories),
        qod_category_list="; ".join(categories) or None,
        qod_brands=len(brands),
        qod_has_po_box=max(p["has_po_box"] for p in pages),
        qod_has_phone=max(p["has_phone"] for p in pages),
    )
    return out
