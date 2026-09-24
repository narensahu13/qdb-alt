"""Customers -> award records.

Four methods, best evidence first. Every match records which one produced
it, so CR matches can be used as they are and the rest treated as needing
a look.

  cr             commercial registration number, digits before any branch
                 suffix. Exact, and the reason this source is worth having:
                 QDB already holds the CR number for every borrower, so this
                 is a join, not a guess.
  name_exact     identical normalised names in the same script (score 1.0),
                 or the same words in a different order (0.99).
  name_fuzzy     token-sort similarity >= the threshold (default 0.92), same
                 script. Needs review.
  name_translit  an Arabic name against a Latin one (or the reverse),
                 compared as consonant skeletons of the whole name. The only
                 way to join an Arabic-only customer without a CR. Needs
                 review.

What this deliberately does not do any more (it did in the first qdb-alt
commit, and it matched borrowers to unrelated companies):
  - score a pair 0.92-1.0 because the first words share consonants
    ("Qatar Packaging" = every "QATAR ..." company);
  - treat equal consonant skeletons as an exact match
    ("Nasser Trading" = "Nisr Trading");
  - compare every customer with every company (hours on a real book).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable, Iterable

from .normalize import (MIN_SKELETON, canonical_key, cr_root, is_arabic,
                        normalize_name, skeleton_forms,
                        token_sort_similarity)

try:
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
except ImportError:                      # pragma: no cover
    _rf_fuzz = _rf_process = None

FUZZY_THRESHOLD = 0.92
TRANSLIT_THRESHOLD = 0.90
REVIEW_METHODS = ("name_fuzzy", "name_translit")
METHODS = ("cr", "name_exact") + REVIEW_METHODS


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
    report: dict[str, Any] = {"file": str(path), "warnings": [], "notes": []}

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
        name_ar = cell(row, "name_ar") or None
        # A row with only an Arabic name is still a customer (it used to be
        # dropped as empty).
        if not name and not cr and not name_ar:
            skipped += 1
            continue
        cid = cell(row, "customer_id") or name or name_ar or cr
        if not name_ar and is_arabic(name):
            name_ar = name
        out.append({"customer_id": cid, "name": name,
                    "cr_number": cr or None,
                    "name_ar": name_ar})

    report["rows"] = len(out)
    report["skipped (no name, no CR)"] = skipped
    # What each row can be matched on. A CR that does not parse is as good
    # as no CR, so it is counted -- and shown -- separately.
    unreadable = [r for r in out if r["cr_number"] and not cr_root(r["cr_number"])]
    no_cr = [r for r in out if not cr_root(r["cr_number"])]
    report["with CR number"] = len(out) - len(no_cr)
    report["no CR, English name"] = sum(
        1 for r in no_cr if r["name"] and not is_arabic(r["name"]))
    report["no CR, Arabic name only"] = sum(
        1 for r in no_cr if (r["name_ar"] or r["name"])
        and not (r["name"] and not is_arabic(r["name"])))
    if unreadable:
        report["warnings"].append(
            f"{len(unreadable)} CR value(s) contain no number, e.g. "
            + ", ".join(repr(r["cr_number"]) for r in unreadable[:3]))
    if "customer_id" not in mapping:
        report["warnings"].append("no customer id column -- using the name "
                                  "(or CR) as the id")
    if "cr_number" not in mapping:
        report["warnings"].append("no CR number column -- matching will rely "
                                  "on names alone, which is much weaker")
    ids = {r["customer_id"] for r in out}
    dupes = len(out) - len(ids)
    if dupes:
        # Not a problem, and usually deliberate: one borrower with several CR
        # numbers (a changed registration, a subsidiary, a branch) gets a row
        # each. Every row is matched and the results are filed under the one
        # customer id. Only two rows landing on the very same company row of
        # the same tender collide, and then the stronger evidence wins (a CR
        # match over a name match).
        report["notes"].append(
            f"{len(ids)} customers over {len(out)} rows -- {dupes} extra "
            "row(s) share a customer id. Each row is matched on its own and "
            "the results are filed under that one id, so several CR numbers "
            "for one borrower all count.")
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
    print(f"  rows          {report['rows']}")
    print(f"    with a CR number          {report['with CR number']:>6}"
          "   joined exactly")
    if report.get("no CR, English name"):
        print(f"    no CR, English name       {report['no CR, English name']:>6}"
              "   joined by name; similar-name matches need review")
    if report.get("no CR, Arabic name only"):
        print(f"    no CR, Arabic name only   "
              f"{report['no CR, Arabic name only']:>6}"
              "   joined by transliteration only; review, or add the CR")
    if report["skipped (no name, no CR)"]:
        print(f"  skipped       {report['skipped (no name, no CR)']} "
              "empty rows")
    for w in report["warnings"]:
        print(f"  warning: {w}")
    for n in report.get("notes", []):
        print(f"  note: {n}")
    print()


def _field(row: Any, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


# --------------------------------------------------------------------------
# matching
#
# The candidate list (company rows, or the classified register) is indexed
# once. A customer is then compared only with names that share one of its
# two rarest words (fuzzy), or whose skeleton is close (translit), instead
# of with every name on the list.
# --------------------------------------------------------------------------

class _Names:
    """Unique normalised names in one script, with a word index."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.rows: list[list[Any]] = []
        self._pos: dict[str, int] = {}
        self.postings: dict[str, list[int]] = {}

    def add(self, norm: str, row: Any) -> None:
        i = self._pos.get(norm)
        if i is None:
            i = self._pos[norm] = len(self.names)
            self.names.append(norm)
            self.rows.append([])
            for tok in set(norm.split()):
                self.postings.setdefault(tok, []).append(i)
        self.rows[i].append(row)

    def candidates(self, query: str, words: int = 2) -> set[int]:
        present = [t for t in set(query.split()) if t in self.postings]
        present.sort(key=lambda t: len(self.postings[t]))
        out: set[int] = set()
        for t in present[:words]:
            out.update(self.postings[t])
        return out


class _Skeletons:
    """Space-free consonant skeletons of names in one script."""

    def __init__(self) -> None:
        self.skels: list[str] = []
        self.rows: list[list[Any]] = []
        self._pos: dict[str, int] = {}
        self._by_prefix: dict[str, list[int]] = {}

    def add(self, sk: str, row: Any) -> None:
        if len(sk) < MIN_SKELETON:
            return
        i = self._pos.get(sk)
        if i is None:
            i = self._pos[sk] = len(self.skels)
            self.skels.append(sk)
            self.rows.append([])
            self._by_prefix.setdefault(sk[:2], []).append(i)
        self.rows[i].append(row)

    def search(self, sk: str, threshold: float) -> list[tuple[int, float]]:
        if len(sk) < MIN_SKELETON or not self.skels:
            return []
        if _rf_process is not None:
            hits = _rf_process.extract(sk, self.skels, scorer=_rf_fuzz.ratio,
                                       score_cutoff=threshold * 100,
                                       limit=None)
            return [(i, score / 100.0) for _, score, i in hits]
        import difflib
        out = []
        for i in self._by_prefix.get(sk[:2], []):
            score = difflib.SequenceMatcher(None, sk, self.skels[i]).ratio()
            if score >= threshold:
                out.append((i, score))
        return out


class NameIndex:
    """CR, exact, fuzzy and transliteration lookups over a list of rows."""

    def __init__(self, rows: Iterable[Any], name_fields=("name",)) -> None:
        self.by_cr: dict[str, list[Any]] = {}
        self.exact: dict[str, list[Any]] = {}
        self.canon: dict[str, list[Any]] = {}
        self.latin = _Names()
        self.arabic = _Names()
        self.skel_latin = _Skeletons()      # searched by Arabic names
        self.skel_arabic = _Skeletons()     # searched by Latin names
        for row in rows:
            root = _field(row, "cr_root") or cr_root(_field(row, "cr_number"))
            if root:
                self.by_cr.setdefault(root, []).append(row)
            seen: set[str] = set()
            for f in name_fields:
                raw = _field(row, f)
                if not raw or raw in seen:
                    continue
                seen.add(raw)
                norm = normalize_name(raw)
                if not norm:
                    continue
                arabic = is_arabic(raw)
                self.exact.setdefault(norm, []).append(row)
                self.canon.setdefault(canonical_key(raw), []).append(row)
                (self.arabic if arabic else self.latin).add(norm, row)
                target = self.skel_arabic if arabic else self.skel_latin
                for sk in skeleton_forms(raw):
                    target.add(sk, row)

    def lookup(self, cust: dict[str, Any], *,
               fuzzy_threshold: float = FUZZY_THRESHOLD,
               translit_threshold: float = TRANSLIT_THRESHOLD,
               ) -> Iterable[tuple[Any, str, float]]:
        """Yield (row, method, score) for one customer, best evidence first."""
        root = cr_root(cust.get("cr_number"))
        if root:
            for row in self.by_cr.get(root, []):
                yield row, "cr", 1.0

        names: list[str] = []
        for raw in (cust.get("name"), cust.get("name_ar")):
            if raw and raw not in names:
                names.append(raw)

        for raw in names:
            norm = normalize_name(raw)
            if not norm:
                continue
            for row in self.exact.get(norm, []):
                yield row, "name_exact", 1.0
            for row in self.canon.get(canonical_key(raw), []):
                yield row, "name_exact", 0.99

            if fuzzy_threshold < 1.0:
                side = self.arabic if is_arabic(raw) else self.latin
                for i in side.candidates(norm):
                    cand = side.names[i]
                    if cand == norm:
                        continue
                    score = token_sort_similarity(norm, cand)
                    if score >= fuzzy_threshold:
                        for row in side.rows[i]:
                            yield row, "name_fuzzy", round(score, 3)

            if translit_threshold < 1.0:
                other = self.skel_latin if is_arabic(raw) else self.skel_arabic
                for sk in skeleton_forms(raw):
                    for i, score in other.search(sk, translit_threshold):
                        for row in other.rows[i]:
                            yield row, "name_translit", round(score, 3)


def _run(customers: Iterable[dict[str, Any]], index: NameIndex,
         key: Callable[[dict[str, Any], Any], tuple],
         emit: Callable[[dict[str, Any], Any, str, float], dict[str, Any]],
         fuzzy_threshold: float, translit_threshold: float
         ) -> list[dict[str, Any]]:
    best: dict[tuple, dict[str, Any]] = {}
    rank = {m: i for i, m in enumerate(METHODS)}
    for cust in customers:
        for row, method, score in index.lookup(
                cust, fuzzy_threshold=fuzzy_threshold,
                translit_threshold=translit_threshold):
            k = key(cust, row)
            old = best.get(k)
            # Better method wins; within a method, the higher score.
            if old is not None and (rank[old["method"]], -old["score"]) <= (
                    rank[method], -score):
                continue
            best[k] = emit(cust, row, method, score)
    return list(best.values())


def match_customers(
    customers: Iterable[dict[str, Any]],
    companies: Iterable[Any],
    *,
    fuzzy_threshold: float = FUZZY_THRESHOLD,
    translit_threshold: float = TRANSLIT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return one row per (customer, tender, role, company row) match.

    Keyed on the company row, not just the tender: a tender can have several
    winners, and a borrower must only ever be credited with its own line.
    Each company row is matched by its best method only.
    """
    index = NameIndex(companies)

    def key(cust, comp):
        return (cust["customer_id"], comp["tender_id"], comp["role"],
                comp["seq"])

    def emit(cust, comp, method, score):
        return {
            "customer_id": cust["customer_id"],
            "customer_name": cust.get("name"),
            "tender_id": comp["tender_id"],
            "role": comp["role"],
            "seq": comp["seq"],
            "method": method,
            "score": score,
            "matched_name": _field(comp, "name"),
            "matched_cr": _field(comp, "cr_number"),
        }

    return _run(customers, index, key, emit, fuzzy_threshold,
                translit_threshold)


def match_classified(
    customers: Iterable[dict[str, Any]],
    register: Iterable[Any],
    *,
    fuzzy_threshold: float = FUZZY_THRESHOLD,
    translit_threshold: float = TRANSLIT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Join customers to the classified-company register."""
    index = NameIndex(register)

    def key(cust, row):
        return (cust["customer_id"], _field(row, "profile_number"))

    def emit(cust, row, method, score):
        return {
            "customer_id": cust["customer_id"],
            "customer_name": cust.get("name"),
            "profile_number": _field(row, "profile_number"),
            "method": method,
            "score": score,
            "matched_name": _field(row, "name"),
            "matched_cr": _field(row, "cr_number"),
            "size": _field(row, "size"),
            "evaluation": _field(row, "evaluation"),
            "certificate_end": _field(row, "certificate_end"),
        }

    return _run(customers, index, key, emit, fuzzy_threshold,
                translit_threshold)


def summarise(matches: list[dict[str, Any]]) -> dict[str, Any]:
    customers = {m["customer_id"] for m in matches}
    by_method = {m: sum(1 for x in matches if x["method"] == m)
                 for m in METHODS}
    return {
        "matches": len(matches),
        "customers matched": len(customers),
        "by method": by_method,
        "needs review": sum(by_method[m] for m in REVIEW_METHODS),
        "awarded": sum(1 for m in matches if m["role"] == "awarded"),
        "bid only": sum(1 for m in matches if m["role"] == "bidder"),
    }
