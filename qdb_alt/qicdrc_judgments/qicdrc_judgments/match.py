"""Joining a customer book to the parties of the court's judgments.

The court publishes no CR numbers, so the join is by name, with the strict rule
in namematch.py (`same_company`): every distinctive word of the customer's
name must be in the party's name and the brand word must match exactly. A
party name is tried whole and also cut at commas and at the word "and": the
parser keeps "Al Wajba Dairy and Juices Factory" in one piece because it is
one company, but a heading may join two companies with "and" in a way the
parser could not tell apart.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .namematch import same_company, search_name

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


@lru_cache(maxsize=None)
def candidates(party: str) -> tuple[str, ...]:
    """'Gulf GRC and Trading Co' -> itself, 'Gulf GRC', 'Trading Co'."""
    pieces = [p.strip() for p in re.split(r",|\band\b", party, flags=re.I) if p.strip()]
    return tuple(dict.fromkeys([party] + pieces))


def match_one(customer: dict[str, str], parties: list[dict[str, Any]]) -> dict[str, Any]:
    me = customer.get("search_name") or search_name(customer["name"])
    hits = [p for p in parties if any(same_company(me, c) for c in candidates(p["name"]))]
    return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
            "cr_number": customer["cr_number"], "hits": hits}


def features(row: dict[str, Any], by_url: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Model-ready columns for one customer: counts are 0 and the rest empty when it is a party to nothing."""
    urls = list(dict.fromkeys(h["url"] for h in row["hits"]))
    js = sorted((by_url[u] for u in urls if u in by_url), key=lambda j: (j["judgment_date"] or "", j["url"]))
    side = lambda s: len({h["url"] for h in row["hits"] if h["side"] == s})
    years = [j["year"] for j in js if j["year"]]
    join = lambda xs: "; ".join(x for x in dict.fromkeys(xs) if x) or None
    return {"customer_id": row["customer_id"], "customer_name": row["customer_name"], "cr_number": row["cr_number"],
            "qicdrc_cases": len(js),
            "qicdrc_distinct_cases": len({j["case_number"] or j["url"] for j in js}),
            "qicdrc_as_claimant": side("claimant"),
            "qicdrc_as_defendant": side("defendant"),
            "qicdrc_last_judgment_year": max(years) if years else None,
            "qicdrc_outcomes": join(j["outcome"] for j in js),
            "qicdrc_matched_parties": join(h["name"] for j in js for h in row["hits"] if h["url"] == j["url"]),
            "qicdrc_citations": join(j["citation"] for j in js),
            "qicdrc_urls": join(j["url"] for j in js)}
