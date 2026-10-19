"""python -m customs_trusted_traders <command>

    load     read both programme pages and store today's lists
    match    join a customer book to the lists, write one row of features per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import fetch, parse
from .match import PROGRAMMES, features, load_customers, match_one
from .store import Store

DEFAULT_DB = "customs_trusted_traders.db"
READERS = {"aeo": parse.aeo, "green_list": parse.green_list}


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def cmd_load(args) -> int:
    session = getattr(args, "session", None)
    if session is None and not args.no_system_trust:
        fetch.use_system_trust_store()
    site = fetch.Site(delay=args.delay, session=session)
    with Store(args.db) as store:
        with store.run("load") as info:
            notes = []
            pages = {p: READERS[p](site.get(fetch.PAGES[p])) for p in PROGRAMMES}   # both read before any write
            for programme, rows in pages.items():
                if not rows:
                    raise fetch.SiteError(f"{programme}: the page lists no companies")
                listed, dropped = store.replace_list(programme, rows)
                info["rows"] += listed
                notes.append(f"{programme} {listed}" + (f" ({dropped} dropped)" if dropped else ""))
            info["note"] = ", ".join(notes)
            store.db.commit()
        print(info["note"])
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        lists = {p: store.traders(p) for p in PROGRAMMES}
        if not all(lists.values()):
            print("The database has no lists yet: run `load` first.", file=sys.stderr)
            return 2
        customers = load_customers(args.customers)
        with store.run("match") as info:
            out, saved = [], []
            for c in customers:
                by_programme = {p: match_one(c, p, lists[p]) for p in PROGRAMMES}
                saved += [{k: v for k, v in m.items() if k != "_trader"} for m in by_programme.values()]
                out.append(features(c, by_programme))
            store.save_matches(saved, info["id"])
            info["rows"] = len(out)
            found = sum(r["customs_aeo"] or r["customs_green_list"] for r in out)
            info["note"] = f"{found} of {len(out)} customers on a list"
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{found} of {len(out)} customers on a list -> {args.out}")
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
    ap = argparse.ArgumentParser(prog="python -m customs_trusted_traders", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="read both programme pages")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default 2)")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="customs_trusted_traders_features.csv")
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
