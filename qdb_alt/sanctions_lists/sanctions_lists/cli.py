"""python -m sanctions_lists <command>

    load     download the official lists (UN, OFAC SDN and non-SDN, EU, UK) and store their entities
    match    screen a customer book against the lists, write one row per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import fetch, parse
from .match import Screener, load_customers
from .store import Store

DEFAULT_DB = "sanctions_lists.db"


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def read_lists(session=None) -> dict[str, list[dict]]:
    """Every list, downloaded and parsed; any failure stops here, before anything is written."""
    get = lambda key: fetch.download(key, session)
    lists = {
        "UN": parse.un(get("un")),
        "OFAC SDN": parse.ofac(get("ofac_sdn"), get("ofac_sdn_alt"), "OFAC SDN"),
        "OFAC non-SDN": parse.ofac(get("ofac_cons"), get("ofac_cons_alt"), "OFAC non-SDN"),
        "EU": parse.eu(get("eu")),
        "UK": parse.uk(get("uk")),
    }
    empty = [k for k, v in lists.items() if not v]
    if empty:
        raise fetch.SiteError(f"no entities read from: {', '.join(empty)}")
    return lists


def cmd_load(args) -> int:
    session = getattr(args, "session", None)
    if session is None and not args.no_system_trust:
        fetch.use_system_trust_store()
    with Store(args.db) as store:
        with store.run("load") as info:
            lists = read_lists(session)
            notes = []
            for lst, entities in lists.items():
                listed, gone = store.replace_list(lst, entities)
                info["rows"] += listed
                notes.append(f"{lst} {listed}" + (f" (-{gone})" if gone else ""))
            info["note"] = ", ".join(notes)
            store.db.commit()
        print(info["note"])
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        names = store.names()
        if not names:
            print("The database has no lists yet: run `load` first.", file=sys.stderr)
            return 2
        screener = Screener(names)
        customers = load_customers(args.customers)
        with store.run("match") as info:
            out = [screener.screen(c) for c in customers]
            info["rows"] = store.save_screening(out, info["id"])
            hits = sum(r["sanctions_hit"] for r in out)
            info["note"] = f"{hits} of {len(out)} customers hit"
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{hits} of {len(out)} customers hit a list -> {args.out}")
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
    ap = argparse.ArgumentParser(prog="python -m sanctions_lists", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="download and store the official lists")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="screen a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="sanctions_lists_screening.csv")
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
