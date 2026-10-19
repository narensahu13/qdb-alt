"""Screening a customer book against the lists.

A hit needs both: the customer's name and a listed name (or alias) must be
near-identical once legal forms are dropped and words are sorted (score 95 of
100), and they must share the brand word. That keeps "Qatar Steel" from
hitting a sanctioned "... Steel" company of another name, and catches spelling
differences of the same name. The best score is kept even when it is not a
hit, so a person can look at the near misses.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from .namematch import brand_match, norm, search_name

THRESHOLD = 95
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


def _key(name: str) -> str:
    return " ".join(sorted(norm(name).split()))


class Screener:
    def __init__(self, names: list[tuple[str, str, str]]):
        self.names = names
        self.keys = [_key(n) for _, _, n in names]

    def screen(self, customer: dict[str, str]) -> dict[str, Any]:
        q = customer.get("search_name") or search_name(customer["name"])
        key = _key(q)
        hits, best = [], 0.0
        if key:
            for _, score, i in process.extract(key, self.keys, scorer=fuzz.ratio, processor=None,
                                               score_cutoff=80, limit=20):
                best = max(best, score)
                lst, _, name = self.names[i]
                if score >= THRESHOLD and brand_match(q, name):
                    hits.append((lst, name))
            if not best:
                best = (process.extractOne(key, self.keys, scorer=fuzz.ratio, processor=None) or (None, 0))[1]
        return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
                "cr_number": customer["cr_number"],
                "sanctions_hit": int(bool(hits)),
                "sanctions_lists": "; ".join(sorted({lst for lst, _ in hits})) or None,
                "matched_name": hits[0][1] if hits else None,
                "best_score": round(best, 1)}
