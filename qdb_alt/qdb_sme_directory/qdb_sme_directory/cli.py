"""python -m qdb_sme_directory <command>

    load     download the directory PDF(s) and read every profile into the database
    match    join a customer book to the directory, write one row of features per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import fetch, parse
from .match import features, load_customers, match_one
from .store import Store

DEFAULT_DB = "qdb_sme_directory.db"
DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data"


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def cmd_load(args) -> int:
    editions = list(fetch.EDITIONS) if args.edition == "all" else [args.edition]
    with Store(args.db) as store:
        with store.run(f"load {args.edition}") as info:
            for edition in editions:
                if args.text:            # an already extracted text layer (tests, or a manual copy)
                    text = Path(args.text).read_text(encoding="utf-8")
                else:
                    if not args.no_system_trust:
                        fetch.use_system_trust_store()
                    pdf = Path(args.pdf) if args.pdf else fetch.download(edition, Path(args.data))
                    text = parse.extract_text(pdf)
                profiles = parse.parse_text(text, edition)
                info["rows"] += store.save_profiles(profiles, info["id"])
                print(f"{edition}: {len(profiles)} profiles")
            store.db.commit()
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        profiles = store.profiles()
        if not profiles:
            print("The database has no profiles yet: run `load` first.", file=sys.stderr)
            return 2
        by_page = {(p["edition"], p["page"]): p for p in profiles}
        customers = load_customers(args.customers)
        with store.run("match") as info:
            rows = [match_one(c, profiles) for c in customers]
            info["rows"] = store.save_matches(rows, info["id"])
            found = sum(r["page"] is not None for r in rows)
            info["note"] = f"{found} of {len(rows)} customers found"
            out = [features(r, by_page) for r in rows]
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{found} of {len(rows)} customers found in the directory -> {args.out}")
    return 0


def cmd_stats(args) -> int:
    with Store(args.db) as store:
        _table(store.stats())
    return 0


def cmd_runs(args) -> int:
    with Store(args.db) as store:
        for r in store.runs(args.limit):
            line = (f"#{r['id']} {r['started_at']} {r['command']:<22} {r['status']:<7} "
                    f"{r['seconds'] or 0:>7.1f}s rows={r['rows']}")
            print(line + (f"  {r['note']}" if r["note"] else "") + (f"\n     {r['error']}" if r["error"] else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m qdb_sme_directory", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="download the PDF(s) and read the profiles")
    p.add_argument("--edition", choices=[*fetch.EDITIONS, "all"], default="manufacturing")
    p.add_argument("--data", default=str(DEFAULT_DATA), help="where the PDFs are kept")
    p.add_argument("--pdf", help="read this PDF instead of downloading")
    p.add_argument("--text", help="read this extracted text instead of a PDF")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="qdb_sme_directory_features.csv")
    p.set_defaults(func=cmd_match)

    sub.add_parser("stats", help="what is in the database").set_defaults(func=cmd_stats)
    p = sub.add_parser("runs", help="the run log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
