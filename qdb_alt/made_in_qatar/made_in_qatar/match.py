"""Joining a customer book to the factory register.

The register has no CR numbers, so the join is by name, with the strict rules
in namematch.py: the factory's name must be close to the customer's and carry
the same brand word ("Al Thuraiya Plastic Factory" is not "Al Rayyan Plastic
Factory"). A name with an abbreviation in brackets -- "Qatar Detergent
Company (QDC)" -- is compared without it.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .namematch import best_match, search_name

THRESHOLD = 88
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


def match_one(customer: dict[str, str], factories: list[dict[str, Any]]) -> dict[str, Any]:
    q = customer.get("search_name") or search_name(customer["name"])
    hit, score = best_match(q, factories, key=lambda f: f["name"] or "", threshold=THRESHOLD)
    return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
            "cr_number": customer["cr_number"], "url": hit["url"] if hit else None,
            "factory_name": hit["name"] if hit else None, "score": round(score, 1) if hit else None}


def features(row: dict[str, Any], by_url: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Model-ready columns for one customer; empty (not zero) where the factory is unknown."""
    f = by_url.get(row["url"]) if row["url"] else None
    return {"customer_id": row["customer_id"], "customer_name": row["customer_name"], "cr_number": row["cr_number"],
            "miq_listed": int(f is not None),
            "miq_sector": f["sector"] if f else None,
            "miq_founded_year": f["founded_year"] if f else None,
            "miq_age_years": date.today().year - f["founded_year"] if f and f["founded_year"] else None,
            "miq_products": f["products"] if f else None,
            "miq_website": f["website"] if f else None,
            "matched_factory": row["factory_name"], "match_score": row["score"], "factory_url": row["url"]}
