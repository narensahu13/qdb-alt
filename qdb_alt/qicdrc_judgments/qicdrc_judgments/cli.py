"""python -m qicdrc_judgments <command>

    load     read robots.txt and the sitemap, fetch new or changed judgment pages (resumable)
    match    join a customer book to the parties, write one row of features per customer
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys
import time

from . import fetch, parse
from .match import features, load_customers, match_one
from .store import Store

DEFAULT_DB = "qicdrc_judgments.db"
COMMIT_EVERY = 25
MAX_FAILED_SHARE = 0.10      # more than this share of pages failing makes the run an error
MAX_FAILED_IN_A_ROW = 10     # the site is down or blocking us: stop rather than wait out the list


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


def _listed_pages(site: fetch.Site) -> list[tuple[str, str]]:
    """The English judgment pages in the sitemap (following a sitemap index if there is one)."""
    pages, children = parse.sitemap(site.get(fetch.SITEMAP))
    for child in children:
        pages += parse.sitemap(site.get(child))[0]
    return list(dict.fromkeys(pages))


def cmd_load(args) -> int:
    t0 = time.monotonic()
    site = _site(args)
    with Store(args.db) as store:
        with store.run("load") as info:
            rules = site.read_robots()
            listed = _listed_pages(site)
            if not listed:
                raise fetch.SiteError("the sitemap lists no judgment pages")
            gone = store.set_listed(listed)
            blocked = sum(not rules.allowed(u) for u, _ in listed)
            known = store.judgment_lastmod()
            todo = [(u, m) for u, m in listed
                    if rules.allowed(u) and (args.refetch or u not in known or known[u] != m)]
            fetched = failed = in_a_row = 0
            stopped = None
            for url, lastmod in todo:
                if args.max_pages is not None and fetched + failed >= args.max_pages:
                    stopped = f"--max-pages {args.max_pages}"
                    break
                if args.max_minutes is not None and time.monotonic() - t0 + site.delay > args.max_minutes * 60:
                    stopped = f"--max-minutes {args.max_minutes:g}"
                    break
                try:
                    judgment = parse.parse_judgment(site.get(url), url)
                except (fetch.SiteError, parse.ParseError) as e:
                    store.record_failure(url, lastmod, str(e))
                    failed += 1
                    in_a_row += 1
                    if in_a_row >= MAX_FAILED_IN_A_ROW:
                        store.db.commit()
                        raise fetch.SiteError(f"{in_a_row} pages in a row failed, the last: {e}") from e
                else:
                    store.save_judgment(judgment, lastmod, info["id"])
                    fetched += 1
                    in_a_row = 0
                if (fetched + failed) % COMMIT_EVERY == 0:
                    store.db.commit()
                    print(f"  {fetched + failed}/{len(todo)} pages", file=sys.stderr)
            left = len(todo) - fetched - failed
            note = [f"{len(listed)} judgments in the sitemap, {fetched} pages fetched"]
            if failed:
                note.append(f"{failed} failed (retried next load)")
            if left:
                note.append(f"{left} left for the next load (stopped at {stopped})")
            if gone:
                note.append(f"{gone} no longer listed")
            if blocked:
                note.append(f"{blocked} disallowed by robots.txt, not fetched")
            info["rows"] = fetched
            info["note"] = ", ".join(note)
            store.db.commit()
            if failed > MAX_FAILED_SHARE * (fetched + failed):
                raise fetch.SiteError(f"{failed} of {fetched + failed} pages failed: "
                                      + "; ".join(r["error"] for r in store.failures()[:3]))
        print(info["note"])
        _table(store.stats())
    return 0


def cmd_match(args) -> int:
    with Store(args.db) as store:
        judgments = store.judgments()
        if not judgments:
            print("The database has no judgments yet: run `load` first.", file=sys.stderr)
            return 2
        by_url = {j["url"]: j for j in judgments}
        parties = store.parties()
        pending = store.pending()
        customers = load_customers(args.customers)
        with store.run("match") as info:
            rows = [match_one(c, parties) for c in customers]
            info["rows"] = store.save_matches(rows, info["id"])
            found = sum(bool(r["hits"]) for r in rows)
            info["note"] = (f"{found} of {len(rows)} customers are a party, in {len(judgments)} judgments"
                            + (f" ({pending} pages not loaded yet)" if pending else ""))
            out = [features(r, by_url) for r in rows]
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["customer_id"])
                w.writeheader()
                w.writerows(out)
    print(f"{info['note']} -> {args.out}")
    if pending:
        print(f"Note: {pending} judgment pages are not loaded yet; run `load` again to complete.", file=sys.stderr)
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
    ap = argparse.ArgumentParser(prog="python -m qicdrc_judgments", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", help="read the sitemap and the judgment pages")
    p.add_argument("--delay", type=float, default=10.0,
                   help="seconds between requests (default 10; never less than robots.txt's Crawl-delay)")
    p.add_argument("--max-pages", type=int, help="stop after this many pages; the next load continues")
    p.add_argument("--max-minutes", type=float, help="stop after this many minutes; the next load continues")
    p.add_argument("--refetch", action="store_true", help="fetch every page again, not only new or changed ones")
    p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number)")
    p.add_argument("customers")
    p.add_argument("--out", default="qicdrc_features.csv")
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
