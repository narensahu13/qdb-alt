"""python -m gleif_lei <command>

    load     read every LEI record of Qatar from the GLEIF API and store today's set
    match    join a customer book to the records, write one row of features per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import fetch, parse
from .match import features, index_by_cr, load_customers, match_one
from .store import Store

DEFAULT_DB = "gleif_lei.db"


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def cmd_load(args) -> int:
    site = fetch.Site(delay=args.delay, session=getattr(args, "session", None))
    with Store(args.db) as store:
        with store.run("load") as info:
            rows = [parse.record(r) for r in site.records()]   # every page read before any write
            if not rows:
                raise fetch.SiteError("the API lists no LEI records for Qatar")
            listed, dropped = store.replace_records(rows)
            info["rows"] = listed
            issued = sum(r["registration_status"] == "ISSUED" for r in rows)
            with_cr = sum(r["cr_root"] is not None for r in rows)
            info["note"] = (f"{listed} records, {issued} issued, {with_cr} with a Qatar CR"
                            + (f" ({dropped} dropped)" if dropped else ""))
            store.db.commit()
        print(info["note"])
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        records = store.records()
        if not records:
            print("The database has no LEI records yet: run `load` first.", file=sys.stderr)
            return 2
        customers = load_customers(args.customers)
        by_cr = index_by_cr(records)
        with store.run("match") as info:
            matches = [match_one(c, records, by_cr) for c in customers]
            store.save_matches([{k: v for k, v in m.items() if k != "_record"} for m in matches], info["id"])
            out = [features(m) for m in matches]
            info["rows"] = len(out)
            found = sum(r["lei_exists"] for r in out)
            by_cr_number = sum(r["match_method"] == "cr" for r in out)
            info["note"] = f"{found} of {len(out)} customers have an LEI ({by_cr_number} by CR)"
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{info['note']} -> {args.out}")
    return 0


def cmd_stats(args) -> int:
    with Store(args.db) as store:
        _table(store.stats())
    return 0


def cmd_runs(args) -> int:
    with Store(args.db) as store:
        for r in store.runs(args.limit):
            line = (f"#{r['id']} {r['started_at']} {r['command']:<8} {r['status']:<7} "
                    f"{r['seconds'] or 0:>7.1f}s rows={r['rows']}")
            print(line + (f"  {r['note']}" if r["note"] else "") + (f"\n     {r['error']}" if r["error"] else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m gleif_lei", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="read every LEI record of Qatar")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="gleif_lei_features.csv")
    p.set_defaults(func=cmd_match)

    sub.add_parser("stats", help="what is in the database").set_defaults(func=cmd_stats)
    p = sub.add_parser("runs", help="the run log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)
    return ap


def main(argv=None, session=None) -> int:
    args = build_parser().parse_args(argv)
    args.session = session
    return args.func(args)
