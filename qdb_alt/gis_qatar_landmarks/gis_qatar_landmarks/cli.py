"""python -m gis_qatar_landmarks <command>

    load     read the landmarks layer of GIS Qatar
    match    join a customer book to the layer, write one row of features per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import fetch, parse
from .match import features, load_customers, match_one
from .store import Store

DEFAULT_DB = "gis_qatar_landmarks.db"


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def cmd_load(args) -> int:
    session = getattr(args, "session", None)
    if session is None and not args.no_system_trust:
        fetch.use_system_trust_store()
    with Store(args.db) as store:
        with store.run("load") as info:
            landmarks = [parse.landmark(f) for f in fetch.landmarks(session)]
            listed, dropped = store.replace_list(landmarks)
            info["rows"] = listed
            info["note"] = f"{listed} landmarks" + (f", {dropped} no longer listed" if dropped else "")
            store.db.commit()
        print(info["note"])
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        landmarks = store.landmarks()
        if not landmarks:
            print("The database has no landmarks yet: run `load` first.", file=sys.stderr)
            return 2
        customers = load_customers(args.customers)
        with store.run("match") as info:
            rows, out = [], []
            for c in customers:
                row, hits = match_one(c, landmarks)
                rows.append(row)
                out.append(features(c, hits))
            info["rows"] = store.save_matches(rows, info["id"])
            found = sum(r["landmark_names"] is not None for r in rows)
            info["note"] = f"{found} of {len(rows)} customers on the map"
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{found} of {len(rows)} customers on the map -> {args.out}")
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
    ap = argparse.ArgumentParser(prog="python -m gis_qatar_landmarks", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="read the landmarks layer")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="gis_qatar_landmarks_features.csv")
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
