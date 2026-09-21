"""Customers -> award records.

Three passes, best evidence first. Every match records which pass produced
it, so you can report on CR matches alone and treat fuzzy name matches as
needing a look.

  cr          commercial registration number, digits before any branch
              suffix. Exact, and the reason this source is worth having:
              QDB already holds the CR number for every borrower, so this is
              a join, not a guess.
  name_exact  identical normalised names.
  name_fuzzy  token-set similarity above the threshold. Reported with the
              score and the matched string so a human can check it.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable

from .normalize import (arabic_to_latin, canonical_key, consonant_skeleton,
                        cr_root, is_arabic, match_keys, normalize_name,
                        similarity)


# --------------------------------------------------------------------------
# loading a customer list
#
# Real customer lists come out of Excel, in whatever encoding the machine
# that saved them used, with whatever column names the source system had.
# The loader accepts .xlsx directly (no encoding question at all), detects
# the encoding of a CSV rather than assuming UTF-8, and maps common header
# variants onto the three fields it needs. It says what it did, so a wrong
# guess is visible rather than silent.
# --------------------------------------------------------------------------

HEADER_ALIASES = {
    "customer_id": {
        "customerid", "custid", "cid", "id", "clientid", "cif", "cifno",
        "cifnumber", "customerno", "customernumber", "customercode",
        "clientcode", "clientno", "borrowerid", "obligorid", "accountno",
    },
    "name": {
        "name", "customername", "companyname", "clientname", "borrower",
        "borrowername", "entityname", "legalname", "tradename", "nameen",
        "englishname", "companynameen", "customernameen", "obligorname",
    },
    "cr_number": {
        "crnumber", "cr", "crno", "crnum", "commercialregistration",
        "commercialregistrationnumber", "commercialregistrationno",
        "commercialregno", "registrationnumber", "regno", "crid",
    },
    "name_ar": {
        "namear", "arabicname", "companynamear", "customernamear",
        "namearabic",
    },
}


def _norm_header(h: str) -> str:
    return "".join(ch for ch in str(h or "").lower() if ch.isalnum())


def _map_headers(headers: list[str]) -> dict[str, str]:
    """{field: actual header}. Exact field names win over aliases."""
    found: dict[str, str] = {}
    normed = {_norm_header(h): h for h in headers if h is not None}
    for field, aliases in HEADER_ALIASES.items():
        if _norm_header(field) in normed:
            found[field] = normed[_norm_header(field)]
            continue
        for alias in aliases:
            if alias in normed:
                found[field] = normed[alias]
                break
    return found


def _arabic_letters(text: str) -> int:
    return sum(1 for ch in text if "ء" <= ch <= "ي")


def detect_encoding(raw: bytes) -> tuple[str, str, list[str]]:
    """Return (text, encoding, warnings).

    Order of preference:
      1. UTF-8, with or without a byte-order mark
      2. UTF-8 with a handful of stray bytes -- a UTF-8 file someone pasted
         a few odd characters into. Decoding the whole thing as a Windows
         code page would mangle every genuine Arabic name in it.
      3. Windows-1256 (Arabic) if the result contains Arabic letters
      4. Windows-1252 (Western) otherwise
    """
    warnings: list[str] = []
    try:
        return raw.decode("utf-8-sig"), "utf-8", warnings
    except UnicodeDecodeError:
        pass

    soft = raw.decode("utf-8-sig", errors="replace")
    bad = soft.count("�")
    good_multibyte = sum(1 for ch in soft if ord(ch) > 127 and ch != "�")
    if bad <= 3 and good_multibyte >= 10 * max(bad, 1):
        lines = [i + 1 for i, line in enumerate(soft.splitlines())
                 if "�" in line]
        warnings.append(f"{bad} undecodable byte(s) replaced on line(s) "
                        f"{', '.join(map(str, lines[:10]))} -- check those "
                        "rows")
        return soft, "utf-8 (with replacements)", warnings

    ar = raw.decode("cp1256", errors="replace")
    if _arabic_letters(ar) >= 3:
        return ar, "windows-1256 (Arabic Excel)", warnings
    return (raw.decode("cp1252", errors="replace"),
            "windows-1252 (Western Excel)", warnings)


def _read_xlsx(path: Path, sheet: str | None) -> tuple[list[str], list[list]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise SystemExit("Reading .xlsx needs openpyxl: pip install openpyxl"
                         ) from exc
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = None
    for row in rows:                      # skip blank rows above the header
        if row and any(c not in (None, "") for c in row):
            header = [str(c).strip() if c is not None else "" for c in row]
            break
    data = [list(r) for r in rows]
    wb.close()
    return header or [], data


def _clean(v) -> str:
    """Excel hands back CR numbers as floats (12345.0). Undo that."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def load_customers_report(path: str | Path, sheet: str | None = None
                          ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a customer list and describe what was done to it."""
    path = Path(path)
    report: dict[str, Any] = {"file": str(path), "warnings": []}

    if path.suffix.lower() in (".xlsx", ".xlsm"):
        headers, data = _read_xlsx(path, sheet)
        report["encoding"] = "excel workbook"
    else:
        raw = path.read_bytes()
        text, enc, warns = detect_encoding(raw)
        report["encoding"] = enc
        report["warnings"] += warns
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(text.splitlines(), dialect)
        rows = [r for r in reader]
        while rows and not any(c.strip() for c in rows[0]):
            rows.pop(0)
        headers = [h.strip() for h in rows[0]] if rows else []
        data = rows[1:]
        report["delimiter"] = repr(getattr(dialect, "delimiter", ","))

    mapping = _map_headers(headers)
    report["columns"] = mapping
    report["headers found"] = headers
    if "name" not in mapping and "cr_number" not in mapping:
        raise ValueError(
            f"{path.name}: no name or CR number column found.\n"
            f"  headers in the file: {headers}\n"
            f"  name accepts:        {sorted(HEADER_ALIASES['name'])[:8]} ...\n"
            f"  cr_number accepts:   {sorted(HEADER_ALIASES['cr_number'])[:8]}"
            " ...\nRename a column, or add one called name / cr_number.")

    idx = {f: headers.index(h) for f, h in mapping.items()}

    def cell(row, field):
        i = idx.get(field)
        return _clean(row[i]) if i is not None and i < len(row) else ""

    out: list[dict[str, Any]] = []
    skipped = 0
    for row in data:
        name = cell(row, "name")
        cr = cell(row, "cr_number")
        if not name and not cr:
            skipped += 1
            continue
        cid = cell(row, "customer_id") or name or cr
        name_ar = cell(row, "name_ar") or None
        if not name_ar and is_arabic(name):
            name_ar = name
        out.append({"customer_id": cid, "name": name,
                    "cr_number": cr or None,
                    "name_ar": name_ar})

    report["rows"] = len(out)
    report["skipped (no name, no CR)"] = skipped
    report["with CR number"] = sum(1 for r in out if r["cr_number"])
    if "customer_id" not in mapping:
        report["warnings"].append("no customer id column -- using the name "
                                  "(or CR) as the id")
    if "cr_number" not in mapping:
        report["warnings"].append("no CR number column -- matching will rely "
                                  "on names alone, which is much weaker")
    dupes = len(out) - len({r["customer_id"] for r in out})
    if dupes:
        report["warnings"].append(f"{dupes} duplicate customer id(s) -- later "
                                  "rows overwrite earlier ones in matches")
    return out, report


def load_customers(path: str | Path, sheet: str | None = None
                   ) -> list[dict[str, Any]]:
    """Customer rows: customer_id, name, cr_number (and name_ar if given)."""
    return load_customers_report(path, sheet)[0]


def print_load_report(report: dict[str, Any]) -> None:
    print(f"customers: {report['file']}")
    print(f"  read as       {report['encoding']}")
    cols = report["columns"]
    for field in ("customer_id", "name", "cr_number", "name_ar"):
        if field in cols:
            print(f"  {field:<13} <- column '{cols[field]}'")
    print(f"  rows          {report['rows']}"
          f"  ({report['with CR number']} with a CR number)")
    if report["skipped (no name, no CR)"]:
        print(f"  skipped       {report['skipped (no name, no CR)']} "
              "empty rows")
    for w in report["warnings"]:
        print(f"  note: {w}")
    print()


def _field(row: Any, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _index_name(row: Any, by_name: dict, by_key: dict) -> None:
    for raw in (_field(row, "name", ""), _field(row, "name_ar", ""),
                _field(row, "name_normalised", "")):
        if not raw:
            continue
        for key in match_keys(raw):
            by_name.setdefault(key, []).append(row)
            by_key.setdefault(key, []).append(row)
        if not is_arabic(raw):
            n = normalize_name(raw)
            if n:
                by_name.setdefault(n, []).append(row)
                by_key.setdefault(canonical_key(raw), []).append(row)


def _customer_keys(cust: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for raw in (cust.get("name"), cust.get("name_ar")):
        if not raw:
            continue
        keys |= match_keys(raw)
        if not is_arabic(raw):
            n = normalize_name(raw)
            if n:
                keys.add(n)
                keys.add(canonical_key(raw))
        else:
            latin = arabic_to_latin(raw)
            if latin:
                keys.add(latin)
                keys.add(canonical_key(latin))
    return {k for k in keys if k}


def match_customers(
    customers: Iterable[dict[str, Any]],
    companies: Iterable[Any],
    *,
    fuzzy_threshold: float = 0.90,
) -> list[dict[str, Any]]:
    """Return one row per (customer, tender, role, company row) match.

    Order: CR number, then exact Latin/Arabic key, then fuzzy. An Arabic
    customer name is transliterated before it is compared to Monaqasat's
    English (often machine-transliterated) company name.
    """
    companies = list(companies)

    by_cr: dict[str, list[Any]] = {}
    by_name: dict[str, list[Any]] = {}
    by_key: dict[str, list[Any]] = {}
    for c in companies:
        root = _field(c, "cr_root") or cr_root(_field(c, "cr_number"))
        if root:
            by_cr.setdefault(root, []).append(c)
        _index_name(c, by_name, by_key)

    matches: dict[tuple[str, str, str, int], dict[str, Any]] = {}

    def record(cust, comp, method, score):
        key = (cust["customer_id"], comp["tender_id"], comp["role"],
               comp["seq"])
        existing = matches.get(key)
        if existing and existing["score"] >= score:
            return
        matches[key] = {
            "customer_id": cust["customer_id"],
            "customer_name": cust["name"],
            "tender_id": comp["tender_id"],
            "role": comp["role"],
            "seq": comp["seq"],
            "method": method,
            "score": score,
            "matched_name": _field(comp, "name"),
            "matched_cr": _field(comp, "cr_number"),
        }

    for cust in customers:
        root = cr_root(cust.get("cr_number"))
        if root:
            for comp in by_cr.get(root, []):
                record(cust, comp, "cr", 1.0)

        names = _customer_keys(cust)
        for key in names:
            for comp in by_name.get(key, []):
                record(cust, comp, "name_exact", 1.0)
            for comp in by_key.get(key, []):
                record(cust, comp, "name_exact", 0.99)

        if names and fuzzy_threshold < 1.0:
            for query in names:
                first = query.split()[0] if query else ""
                if not first:
                    continue
                q_sk = consonant_skeleton(first)
                for cand_name, comps in by_name.items():
                    c0 = cand_name.split()[0] if cand_name else ""
                    same_stem = bool(q_sk) and len(q_sk) >= 3 \
                        and q_sk == consonant_skeleton(c0)
                    if not (same_stem or cand_name.startswith(first)
                            or first in cand_name or c0 in query):
                        continue
                    score = similarity(query, cand_name)
                    if same_stem:
                        score = max(score, similarity(first, c0), 0.92)
                    if score >= fuzzy_threshold:
                        for comp in comps:
                            record(cust, comp, "name_fuzzy", round(score, 3))

    return list(matches.values())


def summarise(matches: list[dict[str, Any]]) -> dict[str, Any]:
    customers = {m["customer_id"] for m in matches}
    return {
        "matches": len(matches),
        "customers matched": len(customers),
        "by method": {
            m: sum(1 for x in matches if x["method"] == m)
            for m in ("cr", "name_exact", "name_fuzzy")
        },
        "awarded": sum(1 for m in matches if m["role"] == "awarded"),
        "bid only": sum(1 for m in matches if m["role"] == "bidder"),
    }


def match_classified(
    customers: Iterable[dict[str, Any]],
    register: Iterable[Any],
    *,
    fuzzy_threshold: float = 0.90,
) -> list[dict[str, Any]]:
    """Join customers to the classified-company register."""
    by_cr: dict[str, list[Any]] = {}
    by_name: dict[str, list[Any]] = {}
    for row in register:
        root = _field(row, "cr_root") or cr_root(_field(row, "cr_number"))
        if root:
            by_cr.setdefault(root, []).append(row)
        _index_name(row, by_name, {})

    hits: dict[tuple[str, Any], dict[str, Any]] = {}

    def record(cust, row, method, score):
        key = (cust["customer_id"], _field(row, "profile_number"))
        existing = hits.get(key)
        if existing and existing["score"] >= score:
            return
        hits[key] = {
            "customer_id": cust["customer_id"],
            "customer_name": cust["name"],
            "profile_number": _field(row, "profile_number"),
            "method": method,
            "score": score,
            "matched_name": _field(row, "name"),
            "matched_cr": _field(row, "cr_number"),
            "size": _field(row, "size"),
            "evaluation": _field(row, "evaluation"),
            "certificate_end": _field(row, "certificate_end"),
        }

    for cust in customers:
        root = cr_root(cust.get("cr_number"))
        if root:
            for row in by_cr.get(root, []):
                record(cust, row, "cr", 1.0)
        names = _customer_keys(cust)
        for key in names:
            for row in by_name.get(key, []):
                record(cust, row, "name_exact", 1.0)
        if names and fuzzy_threshold < 1.0:
            for query in names:
                first = query.split()[0] if query else ""
                if not first:
                    continue
                q_sk = consonant_skeleton(first)
                for cand_name, rows in by_name.items():
                    c0 = cand_name.split()[0] if cand_name else ""
                    same_stem = bool(q_sk) and len(q_sk) >= 3 \
                        and q_sk == consonant_skeleton(c0)
                    if not (same_stem or cand_name.startswith(first)
                            or first in cand_name or c0 in query):
                        continue
                    score = similarity(query, cand_name)
                    if same_stem:
                        score = max(score, similarity(first, c0), 0.92)
                    if score >= fuzzy_threshold:
                        for row in rows:
                            record(cust, row, "name_fuzzy", round(score, 3))
    return list(hits.values())
