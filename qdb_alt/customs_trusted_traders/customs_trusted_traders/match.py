"""Joining a customer book to the two lists.

The lists have no CR numbers, so the join is by name with the strict rule
for name-only sources (`same_company` in namematch.py): every distinctive
word of the customer's name must be in the listed name, the brand word must
be the same word, and a foreign brand in front of ours is not us.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .namematch import same_company, search_name, similarity

PROGRAMMES = ("aeo", "green_list")
HEADERS = {  # accepted column names, as in the customer books used by icv and monaqasat
    "customer_id": {"customer_id", "id", "client_id"},
    "name": {"name", "company_name", "company", "customer_name"},
    "cr_number": {"cr_number", "cr", "cr_no", "commercial_registration"},
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


def match_one(customer: dict[str, str], programme: str, traders: list[dict[str, Any]]) -> dict[str, Any]:
    q = customer.get("search_name") or search_name(customer["name"])
    hits = [t for t in traders if same_company(q, t["name"])]
    best = max(hits, key=lambda t: similarity(q, t["name"]), default=None)
    return {"customer_id": customer["customer_id"], "programme": programme, "customer_name": customer["name"],
            "cr_number": customer["cr_number"], "trader_name": best["name"] if best else None,
            "_trader": best}


def features(customer: dict[str, str], by_programme: dict[str, dict[str, Any]]) -> dict[str, Any]:
    aeo, green = by_programme["aeo"]["_trader"], by_programme["green_list"]["_trader"]
    return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
            "cr_number": customer["cr_number"],
            "customs_aeo": int(aeo is not None),
            "customs_aeo_year": aeo["year"] if aeo else None,
            "customs_aeo_years_held": date.today().year - aeo["year"] if aeo and aeo["year"] else None,
            "customs_green_list": int(green is not None),
            "matched_aeo": aeo["name"] if aeo else None, "matched_green_list": green["name"] if green else None}
