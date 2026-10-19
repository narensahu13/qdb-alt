"""Joining a customer book to the directory.

The directory has no CR numbers, so the join is by name, with the strict
rules in namematch.py (`same_company`): the listing's name must carry every
distinctive word of the customer's name, the brand word exactly, and no
other brand in front ("Abdul Noor Aluminum Extrusion" is not "Qatar Aluminum
Extrusion"). A listing is compared under its page name and under the name in
its web address, which is how a listing whose title is in Arabic is still
found.

One business often has several listings -- branches, showrooms, a listing
per outlet. All listings within 5 points of the closest name are kept as the
same company; `dd_listings` counts them and the other columns come from the
closest one (the most reviewed, on a tie).
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .namematch import same_company, search_name, similarity

KEEP_WITHIN = 5   # similarity points below the best that still count as the same company
HEADERS = {  # accepted column names, as in the customer books used by icv and monaqasat
    "customer_id": {"customer_id", "id", "client_id"},
    "name": {"name", "company_name", "company", "customer_name"},
    "cr_number": {"cr_number", "cr", "cr_no", "commercial_registration"},
    # optional: how to search when the registered name is not the trading name
    "search_name": {"search_name"},
}


def load_customers(path: str | Path) -> list[dict[str, str]]:
    """customer_id, name, cr_number (and search_name) from a CSV; a missing customer_id becomes the row number."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = {field: next((h for h in rows[0] if h and h.strip().lower() in names), None)
            for field, names in HEADERS.items()}
    if not cols["name"]:
        raise ValueError(f"{Path(path).name}: no name column (accepted: {sorted(HEADERS['name'])})")
    cell = lambda r, field: (r.get(cols[field]) or "").strip() if cols[field] else ""
    return [{"customer_id": cell(r, "customer_id") or str(i), "name": cell(r, "name"),
             "cr_number": cell(r, "cr_number"), "search_name": cell(r, "search_name")}
            for i, r in enumerate(rows, start=1) if cell(r, "name")]


def _names(listing: dict[str, Any]) -> list[str]:
    return [n for n in dict.fromkeys((listing["name"], listing["slug_name"])) if n]


def match_one(customer: dict[str, str], listings: list[dict[str, Any]]) -> dict[str, Any]:
    q = customer.get("search_name") or search_name(customer["name"])
    hits = []
    for listing in listings:
        names = [n for n in _names(listing) if same_company(q, n)]
        if names:
            hits.append((max(similarity(q, n) for n in names), listing))
    best_score = max((s for s, _ in hits), default=0.0)
    kept = [(s, li) for s, li in hits if s >= best_score - KEEP_WITHIN]
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "status": "not_found", "url": None, "listing_name": None,
           "score": None, "listings": len(kept)}
    if any(not li["page_read"] for _, li in kept):
        row["status"] = "unknown"          # a page we could not read may be this company: no answer
    elif kept:
        score, best = max(kept, key=lambda h: (h[0], h[1]["reviews"] or 0, -(h[1]["listing_id"] or 0)))
        row.update(status="found", url=best["url"], listing_name=best["name"] or best["slug_name"],
                   score=round(score, 1))
    return row


def features(row: dict[str, Any], by_url: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Model-ready columns for one customer: empty (not zero) where the listing is unknown,
    and every column empty when a candidate page could not be read."""
    li = by_url.get(row["url"]) if row["url"] else None
    unknown = row["status"] == "unknown"
    found = li is not None
    v = lambda key: li[key] if found else None
    year = v("founded_year")
    return {"customer_id": row["customer_id"], "customer_name": row["customer_name"], "cr_number": row["cr_number"],
            "dd_listed": None if unknown else int(found),
            "dd_listings": None if unknown else row["listings"],
            "dd_category": v("category"),
            "dd_category_path": v("category_path"),
            "dd_rating": v("rating"),
            "dd_reviews": v("reviews"),
            "dd_has_phone": v("has_phone"),
            "dd_has_email": v("has_email"),
            "dd_website": v("website"),
            "dd_social_links": len(li["social"].split(",")) if found and li["social"] else (0 if found else None),
            "dd_open_days": v("open_days"),
            "dd_founded_year": year,
            "dd_age_years": date.today().year - year if year else None,
            "dd_last_updated": ((li["lastmod"] or "")[:10] or None) if found else None,
            "dd_address": v("address"),
            "matched_name": row["listing_name"], "match_score": row["score"], "url": row["url"]}
