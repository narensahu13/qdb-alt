"""Joining a customer book to the LEI records.

1. By CR number. Records registered at the Qatar commercial register
   (RA000494) carry the CR number in `registeredAs`; the CR roots are
   compared (leading zeros and a "/N" branch suffix dropped). A CR hit is taken
   only if the names agree as well -- they share a distinctive word, or their
   distinctive words are at least 40% alike. The root of a branch CR
   ("12091/5") is the parent's number, and the parent is another company, so
   a branch must not take its parent's LEI on the number alone.
2. Otherwise by name: the legal name and every other or transliterated name
   of a record, with `best_match` (namematch.py) at 92 -- nearly the same
   name and the same brand word. A record whose Qatar CR differs from the
   customer's is a different registration and is not taken by name.

When several records fit equally well, an issued LEI wins over a lapsed one,
and a lapsed one over a retired or duplicate record.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .namematch import best_match, brand_match, search_name, similarity, special_words
from .parse import cr_root, names

NAME_THRESHOLD = 92
HEADERS = {  # accepted column names, as in the customer books used by icv and monaqasat
    "customer_id": {"customer_id", "id", "client_id"},
    "name": {"name", "company_name", "company", "customer_name"},
    "cr_number": {"cr_number", "cr", "cr_no", "commercial_registration"},
    "search_name": {"search_name"},
}
STATUS_RANK = {"ISSUED": 0, "PENDING_TRANSFER": 1, "PENDING_ARCHIVAL": 1, "LAPSED": 2}


def load_customers(path: str | Path) -> list[dict[str, str]]:
    """customer_id, name, cr_number (and search_name) from a CSV; a missing customer_id becomes the row number."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = {field: next((h for h in rows[0] if h and h.strip().lower() in accepted), None)
            for field, accepted in HEADERS.items()}
    if not cols["name"]:
        raise ValueError(f"{Path(path).name}: no name column (accepted: {sorted(HEADERS['name'])})")
    cell = lambda r, field: (r.get(cols[field]) or "").strip() if cols[field] else ""
    return [{"customer_id": cell(r, "customer_id") or str(i), "name": cell(r, "name"),
             "cr_number": cell(r, "cr_number"), "search_name": cell(r, "search_name")}
            for i, r in enumerate(rows, start=1) if cell(r, "name")]


def aliases(customer: dict[str, str]) -> list[str]:
    """The customer's names: as written, the search name, and any text in brackets ("(QSIF)")."""
    name = customer["name"]
    out = [name, customer.get("search_name") or "", search_name(name)] + re.findall(r"\((.*?)\)", name)
    return list(dict.fromkeys(a.strip() for a in out if a.strip()))


def names_agree(ours: list[str], theirs: list[str]) -> bool:
    """Do two sets of names point at the same company? Generic words (QATAR, INDUSTRIES,
    FACTORY ...) are left out, so that "Qatar Steel Industries Factory" and "Qatar Plastic
    Industries Factory" do not agree just because three of four words are the same."""
    for a in ours:
        for b in theirs:
            sa, sb = special_words(a), special_words(b)
            if not sa or not sb:                                   # nothing distinctive: whole names
                if similarity(a, b) >= 80:
                    return True
                continue
            if set(sa) & set(sb) or brand_match(a, b) or brand_match(b, a):
                return True
            if fuzz.token_sort_ratio(" ".join(sa), " ".join(sb)) >= 40:
                return True
    return False


def _ordered(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issued first, then lapsed, then the rest; within each, the most recently updated first."""
    recent = sorted(records, key=lambda r: r["last_update"] or "", reverse=True)
    return sorted(recent, key=lambda r: STATUS_RANK.get(r["registration_status"], 3))


def match_one(customer: dict[str, str], records: list[dict[str, Any]],
              by_cr: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """The customer's LEI record, if any: {customer_id, ..., lei, matched_name, match_method, _record}."""
    if by_cr is None:
        by_cr = index_by_cr(records)
    ours, cr = aliases(customer), cr_root(customer["cr_number"])
    best, method, matched = None, None, None
    for rec in _ordered(by_cr.get(cr, []) if cr else []):
        if names_agree(ours, names(rec)):
            best, method, matched = rec, "cr", rec["legal_name"]
            break
    if best is None:
        q = customer.get("search_name") or search_name(customer["name"])
        pairs = [(rec, n) for rec in _ordered(records)
                 if not (cr and rec["cr_root"] and rec["cr_root"] != cr)
                 for n in names(rec)]
        hit, _ = best_match(q, pairs, key=lambda p: p[1], threshold=NAME_THRESHOLD)
        if hit:
            best, method, matched = hit[0], "name", hit[1]
    return {"customer_id": customer["customer_id"], "customer_name": customer["name"],
            "cr_number": customer["cr_number"], "lei": best["lei"] if best else None,
            "matched_name": matched, "match_method": method, "_record": best}


def index_by_cr(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if rec["cr_root"]:
            out.setdefault(rec["cr_root"], []).append(rec)
    return out


def _years_since(day: str | None, today: date) -> float | None:
    if not day:
        return None
    return round((today - date.fromisoformat(day)).days / 365.25, 1)


def features(m: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    rec, today = m["_record"], today or date.today()
    out = {"customer_id": m["customer_id"], "customer_name": m["customer_name"], "cr_number": m["cr_number"],
           "lei_exists": int(rec is not None), "lei_status_issued": None, "lei_years_since_registration": None,
           "lei_lapsed": None, "lei_legal_form": None, "matched_name": None, "match_method": None, "lei": None}
    if rec:
        out.update({"lei_status_issued": int(rec["registration_status"] == "ISSUED"),
                    "lei_years_since_registration": _years_since(rec["initial_registration"], today),
                    "lei_lapsed": int(rec["registration_status"] == "LAPSED"),
                    "lei_legal_form": rec["legal_form"], "matched_name": m["matched_name"],
                    "match_method": m["match_method"], "lei": rec["lei"]})
    return out
