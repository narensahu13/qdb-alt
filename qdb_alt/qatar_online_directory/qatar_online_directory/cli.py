"""python -m qatar_online_directory <command>

    search   search the site for every customer and read the matching listings (cached 30 days)
    match    join a customer book to what was fetched, write one row of features per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import fetch, parse
from .match import features, load_customers, match_one, pick, query_for
from .store import Store

DEFAULT_DB = "qatar_online_directory.db"
MAX_AGE_DAYS = 30
MAX_PAGES = 5            # result pages read per search; a full company name rarely fills one
GIVE_UP_AFTER = 5        # consecutive failed requests: the site is down or refusing us, stop asking


class SiteUnavailable(RuntimeError):
    pass


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def _error(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"[:500]


def cmd_search(args, session=None) -> int:
    customers = load_customers(args.customers)
    if not args.no_system_trust:
        fetch.use_system_trust_store()
    client = fetch.Client(session, pause=args.pause)
    queries = list(dict.fromkeys(query_for(c) for c in customers))
    n = {"asked": 0, "cached": 0, "search_failed": 0, "pages_read": 0, "pages_failed": 0, "listed": 0}
    streak = 0

    def attempt(what, *a):
        nonlocal streak
        try:
            out = what(*a)
        except Exception:
            streak += 1
            if streak >= GIVE_UP_AFTER:
                raise SiteUnavailable(f"{GIVE_UP_AFTER} requests in a row failed; stopped") from None
            raise
        streak = 0
        return out

    with Store(args.db) as store:
        with store.run(f"search {len(queries)}") as info:
            try:
                for i, q in enumerate(queries, start=1):
                    if store.search_is_fresh(q, args.max_age_days):
                        n["cached"] += 1
                    else:
                        n["asked"] += 1
                        try:
                            listings, pages = parse.parse_search(attempt(client.search, q))
                            for page in range(2, min(pages, args.max_pages) + 1):
                                more, _ = parse.parse_search(attempt(client.search, q, page))
                                listings += [l for l in more if l["listing_id"] not in {x["listing_id"] for x in listings}]
                            store.save_search(q, listings, pages, info["id"])
                        except SiteUnavailable:
                            raise
                        except Exception as e:
                            n["search_failed"] += 1
                            store.save_search(q, None, None, info["id"], error=_error(e))
                        info["rows"] += 1
                    s = store.search(q)
                    hits = pick(q, s["listings"]) if s and s["status"] == "ok" else []
                    n["listed"] += bool(hits)
                    for l in hits:
                        if store.company_is_fresh(l["listing_id"], args.max_age_days):
                            continue
                        try:
                            fields = parse.parse_company(attempt(client.company, l["url"]))
                            store.save_company(l["listing_id"], l["url"], fields, info["id"])
                            n["pages_read"] += 1
                        except SiteUnavailable:
                            raise
                        except Exception as e:
                            n["pages_failed"] += 1
                            store.save_company(l["listing_id"], l["url"], None, info["id"], error=_error(e))
                        info["rows"] += 1
                    store.db.commit()
                    if i % 25 == 0:
                        print(f"  {i}/{len(queries)} names, {n['listed']} listed, "
                              f"{n['search_failed']} searches failed", flush=True)
            finally:
                info["note"] = (f"{len(queries)} names: {n['asked']} searched, {n['cached']} from cache, "
                                f"{n['search_failed']} searches failed; {n['listed']} listed; "
                                f"{n['pages_read']} listing pages read, {n['pages_failed']} failed")
                store.db.commit()
        _table(store.stats())
    print(info["note"])
    return 0


def cmd_match(args) -> int:
    customers = load_customers(args.customers)
    with Store(args.db) as store:
        with store.run("match") as info:
            rows = [match_one(c, store.search(query_for(c))) for c in customers]
            companies = {l["listing_id"]: store.company(l["listing_id"]) for r in rows for l in r["listings"]}
            companies = {k: v for k, v in companies.items() if v}
            info["rows"] = store.save_matches(rows, info["id"])
            found = sum(r["status"] == "found" for r in rows)
            unsearched = sum(r["status"] == "no search" for r in rows)
            info["note"] = (f"{found} of {len(rows)} customers found"
                            + (f"; {unsearched} without a search (not searched yet or failed)" if unsearched else ""))
            out = [features(r, companies) for r in rows]
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{info['note']} -> {args.out}")
    if unsearched:
        print("Run `search` on the same file to fill the empty rows.", file=sys.stderr)
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
    ap = argparse.ArgumentParser(prog="python -m qatar_online_directory", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="search the site for a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--max-age-days", type=float, default=MAX_AGE_DAYS,
                   help=f"re-use what was fetched within this many days (default {MAX_AGE_DAYS})")
    p.add_argument("--max-pages", type=int, default=MAX_PAGES, help="result pages read per search")
    p.add_argument("--pause", type=float, default=fetch.PAUSE,
                   help=f"seconds between requests (default {fetch.PAUSE:g}, the site's crawl delay)")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("match", help="write features for a customer book from what `search` fetched")
    p.add_argument("customers")
    p.add_argument("--out", default="qatar_online_directory_features.csv")
    p.set_defaults(func=cmd_match)

    sub.add_parser("stats", help="what is in the database").set_defaults(func=cmd_stats)
    p = sub.add_parser("runs", help="the run log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)
    return ap


def main(argv=None, session=None) -> int:
    """`session` replaces the HTTP session (the tests pass one that serves fixtures)."""
    args = build_parser().parse_args(argv)
    if args.func is cmd_search:
        try:
            return cmd_search(args, session)
        except SiteUnavailable as e:      # already in the run log; what was fetched is kept
            print(f"{e}. Run `search` again later: finished names are not asked twice.", file=sys.stderr)
            return 1
    return args.func(args)
