"""Joining a customer book to the landmarks layer.

The layer has no CR numbers, so the join is by name with the strict rule for
name-only sources (`same_company` in namematch.py). A company can be on the
map more than once -- a factory and a showroom, or a chain's branches -- so
every landmark that matches is counted.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .namematch import same_company, search_name

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


def match_one(customer: dict[str, str], landmarks: list[dict[str, Any]]) -> tuple[dict[str, Any], list]:
    q = customer.get("search_name") or search_name(customer["name"])
    hits = [m for m in landmarks if same_company(q, m["name"])]
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "landmark_ids": [m["id"] for m in hits],
           "landmark_names": "; ".join(dict.fromkeys(m["name"] for m in hits)) or None}
    return row, hits


def _joined(values) -> str | None:
    return "; ".join(dict.fromkeys(v for v in values if v)) or None


def features(customer: dict[str, str], hits: list[dict[str, Any]]) -> dict[str, Any]:
    return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
            "cr_number": customer["cr_number"],
            "gis_landmark": int(bool(hits)),
            "gis_landmarks": len(hits) if hits else None,
            "gis_categories": _joined(sorted({m["category"] for m in hits if m["category"]})),
            "gis_districts": _joined(m["district"] for m in hits),
            "matched_landmarks": _joined(m["name"] for m in hits)}
