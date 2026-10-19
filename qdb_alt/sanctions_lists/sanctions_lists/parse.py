"""From each official list's own file format to one record per sanctioned entity.

Only entities (companies, organisations) are kept: the customer book is a
book of companies, and screening a company name against individuals' names
produces noise, not hits. Vessels and aircraft are dropped too.

Each record: list, reference (the list's own id), name, aliases, programme,
listing date when the list gives one.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Any


def _clean(s: str | None) -> str:
    return " ".join((s or "").replace("-0-", " ").split())


def _record(lst: str, ref: str, name: str, aliases, programme: str | None, listed_on: str | None) -> dict[str, Any]:
    names = list(OrderedDict.fromkeys(a for a in (_clean(x) for x in aliases) if a and a != name))
    return {"list": lst, "ref": ref, "name": name, "aliases": names,
            "programme": _clean(programme) or None, "listed_on": (listed_on or "")[:10] or None}


# ---------- UN Security Council consolidated list (XML) ----------
def un(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    out = []
    for e in root.iter("ENTITY"):
        name = _clean(e.findtext("FIRST_NAME"))
        if not name:
            continue
        aliases = [a.findtext("ALIAS_NAME") for a in e.iter("ENTITY_ALIAS")]
        out.append(_record("UN", _clean(e.findtext("REFERENCE_NUMBER")), name, aliases,
                           e.findtext("UN_LIST_TYPE"), e.findtext("LISTED_ON")))
    return out


# ---------- OFAC (US Treasury): SDN list and the non-SDN consolidated list (CSV, no header) ----------
OFAC_SKIP = {"individual", "vessel", "aircraft"}


def ofac(main_csv: str, alt_csv: str, lst: str) -> list[dict[str, Any]]:
    """main: ent_num, name, type, programme, ...; alt: ent_num, alt_num, alt_type, alt_name, remarks."""
    aliases: dict[str, list[str]] = {}
    for row in csv.reader(io.StringIO(alt_csv)):
        if len(row) >= 4 and row[0].strip().isdigit():
            aliases.setdefault(row[0].strip(), []).append(row[3])
    out = []
    for row in csv.reader(io.StringIO(main_csv)):
        if len(row) < 4 or not row[0].strip().isdigit():
            continue                          # the end-of-file marker line
        kind = _clean(row[2]).lower()
        if kind in OFAC_SKIP:
            continue
        out.append(_record(lst, row[0].strip(), _clean(row[1]), aliases.get(row[0].strip(), []), row[3], None))
    return out


# ---------- EU financial sanctions (CSV, ';', one row per name x address x ...) ----------
def eu(csv_text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")), delimiter=";")
    by_id: dict[str, dict[str, Any]] = OrderedDict()
    for row in reader:
        if (row.get("Entity_SubjectType") or "").strip().lower() not in ("e", "enterprise"):   # P = person
            continue
        eid = (row.get("Entity_LogicalId") or "").strip()
        rec = by_id.setdefault(eid, {"names": [], "programme": row.get("Entity_Regulation_Programme"),
                                     "listed_on": row.get("Entity_DesignationDate") or
                                     row.get("Entity_Regulation_PublicationDate"),
                                     "ref": (row.get("Entity_EU_ReferenceNumber") or "").strip() or eid})
        name = _clean(row.get("NameAlias_WholeName"))
        if name and name not in rec["names"]:
            rec["names"].append(name)
    return [_record("EU", r["ref"], r["names"][0], r["names"][1:], r["programme"], r["listed_on"])
            for r in by_id.values() if r["names"]]


# ---------- UK sanctions list (FCDO, CSV with a report-date line before the header) ----------
def uk(csv_text: str) -> list[dict[str, Any]]:
    lines = csv_text.lstrip("\ufeff").splitlines()
    start = next(i for i, l in enumerate(lines) if re.search(r"\bName 6\b", l))   # skip "Report Date: ..."
    reader = csv.DictReader(lines[start:])
    by_id: dict[str, dict[str, Any]] = OrderedDict()
    for row in reader:
        kind = (row.get("Group Type") or row.get("Designation Type") or row.get("Entity Type") or "").strip().lower()
        if kind not in ("entity", "organisation", "organization"):
            continue
        gid = (row.get("Unique ID") or row.get("Group ID") or row.get("OFSI Group ID") or "").strip()
        parts = [row.get(f"Name {i}") for i in (6, 1, 2, 3, 4, 5)]
        name = _clean(" ".join(p for p in parts if p and p.strip()))
        rec = by_id.setdefault(gid, {"names": [], "programme": row.get("Regime") or row.get("Regime Name"),
                                     "listed_on": _uk_date(row.get("Date Designated") or row.get("Listed On"))})
        if name and name not in rec["names"]:
            rec["names"].append(name)
    return [_record("UK", gid, r["names"][0], r["names"][1:], r["programme"], r["listed_on"])
            for gid, r in by_id.items() if r["names"]]


def _uk_date(s: str | None) -> str | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", (s or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
