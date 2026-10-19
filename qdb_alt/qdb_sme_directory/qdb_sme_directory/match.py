"""Joining a customer book to the directory.

The directory has no CR numbers, so the join is by name, with the strict rules
in namematch.py: the customer's name against each profile's name and each
title-like line of the page, at one threshold.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .namematch import best_match, search_name

THRESHOLD = 90
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


def match_one(customer: dict[str, str], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    q = customer.get("search_name") or search_name(customer["name"])
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "edition": None, "page": None,
           "profile_name": None, "score": None, "method": None}
    # the name, and each title-like line on its own: a wrapped name ("Al Raed Fresh Food Processing" /
    # "Factory") is closer to the customer's name line by line than joined
    candidates = [(p, p["name"]) for p in profiles if p["name"]] + [(p, t) for p in profiles for t in p["titles"]]
    hit, score = best_match(q, candidates, key=lambda pt: pt[1], threshold=THRESHOLD)
    if hit is not None:
        p, text = hit
        row.update(edition=p["edition"], page=p["page"], profile_name=p["name"] or text,
                   score=round(score, 1), method="name" if text == p["name"] else "title line")
    return row


def features(row: dict[str, Any], by_page: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
    """Model-ready columns for one matched (or unmatched) customer."""
    p = by_page.get((row["edition"], row["page"])) if row["page"] is not None else None
    return {"customer_id": row["customer_id"], "customer_name": row["customer_name"], "cr_number": row["cr_number"],
            "qdb_dir_listed": int(p is not None),
            "qdb_dir_edition": p["edition"] if p else None,
            "qdb_dir_exporter": p["exporter"] if p else None,
            "qdb_dir_hs_codes": len(p["hs_codes"]) if p else None,
            "qdb_dir_founded_year": p["founded_year"] if p else None,
            "qdb_dir_website": p["website"] if p else None,
            "matched_profile": row["profile_name"], "match_score": row["score"], "match_method": row["method"]}
