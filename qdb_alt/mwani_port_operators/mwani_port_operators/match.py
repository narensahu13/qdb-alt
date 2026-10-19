"""Joining a customer book to the list of licensed port operators.

The list has no CR numbers, so the join is by name with the strict rule for
name-only sources (`same_company` in namematch.py).
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .namematch import same_company, search_name, similarity

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


def match_one(customer: dict[str, str], operators: list[dict[str, Any]]) -> tuple[dict[str, Any], dict | None]:
    q = customer.get("search_name") or search_name(customer["name"])
    hits = [o for o in operators if same_company(q, o["name"])]
    best = max(hits, key=lambda o: similarity(q, o["name"]), default=None)
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "operator_id": best["id"] if best else None,
           "operator_name": best["name"] if best else None}
    return row, best


def features(customer: dict[str, str], op: dict[str, Any] | None) -> dict[str, Any]:
    since = int(op["listed_since"][:4]) if op and op["listed_since"] else None
    return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
            "cr_number": customer["cr_number"],
            "mwani_authorized": int(op is not None),
            "mwani_licences": len(op["licences"]) if op else None,
            "mwani_licence_types": "; ".join(op["licences"]) if op else None,
            "mwani_years_listed": date.today().year - since if since else None,
            "matched_operator": op["name"] if op else None}
