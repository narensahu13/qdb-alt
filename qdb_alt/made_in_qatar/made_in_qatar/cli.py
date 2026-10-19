"""python -m made_in_qatar <command>

    load     read the factories sitemap, fetch new or changed factory pages, read every factory
    match    join a customer book to the register, write one row of features per customer
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

DEFAULT_DB = "made_in_qatar.db"


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def _site(args) -> fetch.Site:
    # `session` is never set from the command line; the tests put a stand-in site there.
    session = getattr(args, "session", None)
    if session is None and not args.no_system_trust:
        fetch.use_system_trust_store()
    return fetch.Site(delay=args.delay, session=session)


def cmd_load(args) -> int:
    site = _site(args)
    with Store(args.db) as store:
        with store.run("load") as info:
            pages = parse.sitemap(site.get(fetch.SITEMAP))
            if not pages:
                raise fetch.SiteError("the factories sitemap lists no factory pages")
            known = store.page_lastmod()
            todo = [(u, m) for u, m in pages if args.refetch or known.get(u) != m]
            for k, (url, lastmod) in enumerate(todo, 1):
                store.save_page(url, lastmod, site.get(url))
                if k % 25 == 0:
                    store.db.commit()
                    print(f"  {k}/{len(todo)} pages", file=sys.stderr)
            for url, _ in pages:      # re-read every listed page: parsing changes apply to the whole register
                store.save_factory(parse.parse_factory(store.page_html(url), url), info["id"])
            gone = store.mark_unlisted({u for u, _ in pages})
            info["rows"] = len(pages)
            info["note"] = f"{len(pages)} factories, {len(todo)} pages fetched, {gone} no longer listed"
            store.db.commit()
        print(info["note"])
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        factories = store.factories()
        if not factories:
            print("The database has no factories yet: run `load` first.", file=sys.stderr)
            return 2
        by_url = {f["url"]: f for f in factories}
        customers = load_customers(args.customers)
        with store.run("match") as info:
            rows = [match_one(c, factories) for c in customers]
            info["rows"] = store.save_matches(rows, info["id"])
            found = sum(r["url"] is not None for r in rows)
            info["note"] = f"{found} of {len(rows)} customers found"
            out = [features(r, by_url) for r in rows]
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{found} of {len(rows)} customers found in the register -> {args.out}")
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
    ap = argparse.ArgumentParser(prog="python -m made_in_qatar", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="read the sitemap and the factory pages")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default 2)")
    p.add_argument("--refetch", action="store_true", help="fetch every page again, not only changed ones")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="made_in_qatar_features.csv")
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
