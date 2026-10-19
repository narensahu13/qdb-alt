"""Joining a customer book to the private-schools layer.

Only customers whose name says they are a school (SCHOOL, ACADEMY,
KINDERGARTEN, COLLEGE, NURSERY, EDUCATION) are looked up; for anyone else
the columns stay empty -- not 0, because a factory is not "an unregistered
school".

The layer has no CR numbers, so the join is by name. A school matches when
it passes the strict rule for name-only sources (`same_company` in
namematch.py), or when its brand -- the first word that is neither generic
nor a school word -- is the customer's: the layer uses short names
("Awfaz International") and adds the campus ("Edison International
Academy-Dahl Al Hamam"). The brand must be the school's first such word:
"Newton International Kindergarten - Oryx" is Newton's, not Oryx's.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .namematch import GENERIC, norm, same_company, search_name
from .parse import LEVELS

HEADERS = {  # accepted column names, as in the customer books used by icv and monaqasat
    "customer_id": {"customer_id", "id", "client_id"},
    "name": {"name", "company_name", "company", "customer_name"},
    "cr_number": {"cr_number", "cr", "cr_no", "commercial_registration"},
    "search_name": {"search_name"},
}

SCHOOL_WORDS = re.compile(r"\b(SCHOOLS?|ACADE?MY|KINDERGA?R?TE?N|COLLEGE|NURSERY|EDUCATION(AL)?)\b", re.I)


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


def is_school(customer: dict[str, str]) -> bool:
    return bool(SCHOOL_WORDS.search(customer["name"]) or SCHOOL_WORDS.search(customer.get("search_name") or ""))


def brand(name: str | None) -> str | None:
    """The first word that is neither generic nor a school word (AWFAZ, EDISON, NOBLE);
    a glued article does not count (ALHAMMAD = HAMMAD)."""
    words = [w for w in norm(name).split() if w not in GENERIC and not SCHOOL_WORDS.fullmatch(w) and len(w) > 2]
    return re.sub(r"^AL(?=\w{3})", "", words[0]) if words else None


def campus(school: dict[str, Any]) -> tuple[str, str | None]:
    """The layer repeats some schools (one record per building); a campus is a name in a district."""
    return school["name"].lower(), school["district"]


def match_one(customer: dict[str, str], schools: list[dict[str, Any]]) -> tuple[dict[str, Any], list | None]:
    """(row for the match table, matched schools); the schools are None when the customer was not looked up."""
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"], "searched": 0, "school_ids": [], "school_names": None}
    if not is_school(customer):
        return row, None
    q = customer.get("search_name") or search_name(customer["name"])
    mine = brand(q)
    hits = [s for s in schools if same_company(q, s["name"]) or (mine and brand(s["name"]) == mine)]
    names = list(dict.fromkeys(s["name"] for s in hits))
    row.update(searched=1, school_ids=[s["id"] for s in hits], school_names="; ".join(names) or None)
    return row, hits


def _union(lists) -> str | None:
    return "; ".join(dict.fromkeys(x for xs in lists for x in xs)) or None


def features(customer: dict[str, str], hits: list[dict[str, Any]] | None) -> dict[str, Any]:
    row = {"customer_id": customer["customer_id"], "customer_name": customer["name"],
           "cr_number": customer["cr_number"],
           "school_registered": None, "school_campuses": None, "school_open": None, "school_levels": None,
           "school_curricula": None, "school_districts": None, "matched_schools": None}
    if hits is None:
        return row
    row["school_registered"] = int(bool(hits))
    if hits:
        taught = {lv for s in hits for lv in s["levels"]}
        row.update(
            school_campuses=len({campus(s) for s in hits}),
            school_open=int(any(s["status"] == "Open" for s in hits)),
            school_levels="; ".join([lv for lv in LEVELS if lv in taught] + sorted(taught - set(LEVELS))),
            school_curricula=_union(s["curricula"] for s in hits),
            school_districts=_union([s["district"]] if s["district"] else [] for s in hits),
            matched_schools="; ".join(dict.fromkeys(s["name"] for s in hits)))
    return row
