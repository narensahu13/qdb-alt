"""python -m qatar_news <command>

    load     read the papers' sitemaps into a headline index (only what is new or changed)
    match    find each customer in the headlines and in Gulf Times' search, one row of features each
    stats    what is in the database
    runs     the run log: when each command ran, how long, rows written, errors
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter, defaultdict

from . import fetch, parse
from .fetch import Disallowed, PageGone, SiteError
from .match import Headlines, anchor, features, is_generic, load_customers, match_one, phrase, query_for
from .parse import PAPERS, Incomplete
from .store import Store, month_settled

DEFAULT_DB = "qatar_news.db"
MAX_SEARCH_FAILURES = 5    # in a row: Gulf Times is not answering, the rest of the book is not searched


def _table(rows) -> None:
    rows = list(rows)
    width = max((len(str(k)) for k, _ in rows), default=0)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


def _site(args) -> fetch.Site:
    # `session` and `timer` are never set from the command line; the tests put a stand-in site and clock there.
    session = getattr(args, "session", None)
    if session is None and not args.no_system_trust:
        fetch.use_system_trust_store()
    return fetch.Site(delay=args.delay, session=session, timer=args.timer)


def _papers(value: str) -> list[str]:
    keys = [k.strip() for k in value.split(",") if k.strip()]
    unknown = [k for k in keys if k not in PAPERS]
    if unknown or not keys:
        raise argparse.ArgumentTypeError(f"papers are {', '.join(PAPERS)} (comma-separated); got {value!r}")
    return [k for k in PAPERS if k in keys]


# ---- load -------------------------------------------------------------------

def _read_whole(site: fetch.Site, url: str):
    """A sitemap that arrives cut off is fetched once more; if it is still not whole, Incomplete."""
    try:
        return parse.read_sitemap(site.get_bytes(url))
    except Incomplete:
        pass
    try:
        return parse.read_sitemap(site.get_bytes(url))
    except Incomplete as e:
        raise Incomplete(f"{url}: {e}") from e


def _due(child: parse.Child, rec: dict | None, paper: parse.Paper, refetch: bool) -> bool:
    """Should this month's sitemap be fetched? Not once a whole copy was read after the month
    ended -- unless the paper's index says it changed since."""
    if refetch or rec is None or not rec["complete"]:
        return True
    if not month_settled(child.period, rec["fetched_at"]):
        return True
    return paper.real_lastmod and bool(child.lastmod) and child.lastmod != rec["lastmod"]


class _Stop(Exception):
    pass


def _load_paper(site, store: Store, paper: parse.Paper, known: dict, args, deadline) -> tuple[Counter, list[str], int]:
    """Returns (counts, errors, sitemaps still to read when stopped)."""
    n: Counter = Counter()
    errors: list[str] = []

    def over() -> bool:
        return deadline is not None and site.timer.monotonic() >= deadline

    def read(child: parse.Child) -> int | None:
        """Fetch one sitemap of articles and store it; the number of new articles, None if it failed."""
        try:
            kind, root = _read_whole(site, child.url)
            if kind != "urls":
                raise Incomplete(f"{child.url}: an index where a list of articles was expected")
            rows = parse.articles(paper, root)
            new = store.save_articles(rows, child.url)
            store.sitemap_read(child.url, paper.name, child.kind, child.period, child.lastmod, len(rows))
            n["read"] += 1
            n["new"] += new
            return new
        except Disallowed as e:
            store.sitemap_failed(child.url, paper.name, child.kind, child.period, "disallowed", str(e))
            n["disallowed"] += 1
        except PageGone as e:          # listed in the index, gone from the site
            store.sitemap_failed(child.url, paper.name, child.kind, child.period, "gone", str(e))
            n["gone"] += 1
        except (SiteError, Incomplete) as e:
            store.sitemap_failed(child.url, paper.name, child.kind, child.period, "failed", str(e))
            n["failed"] += 1
            errors.append(f"{paper.name}: {e}")
        finally:
            store.db.commit()
            if sum(n[k] for k in ("read", "failed", "gone", "disallowed")) % 10 == 0:
                print(f"  {paper.name}: {n['read']} sitemaps read, {n['new']} new articles, {n['failed']} failed",
                      file=sys.stderr)
        return None

    if over():
        return n, errors, -1
    # the index, and the archive indexes it points to: read on every load
    try:
        kind, root = _read_whole(site, paper.index)
        if kind != "index":
            raise Incomplete(f"{paper.index}: not a sitemap index")
        todo: list[parse.Child] = []
        for child in parse.children(paper, root):
            if child.kind != "index":
                todo.append(child)
                continue
            if over():
                raise _Stop
            try:
                kind, sub = _read_whole(site, child.url)
                todo += [c for c in parse.children(paper, sub) if c.kind == "month"] if kind == "index" else []
            except Disallowed:
                n["disallowed"] += 1
            except PageGone:
                n["gone"] += 1
            except (SiteError, Incomplete) as e:
                n["failed"] += 1
                errors.append(f"{paper.name}: {e}")
    except _Stop:
        return n, errors, -1
    except (SiteError, Incomplete) as e:
        n["failed"] += 1
        errors.append(f"{paper.name}: {e}")
        return n, errors, 0

    months = [c for c in todo if c.kind == "month"]
    feeds: dict[str, list[parse.Child]] = defaultdict(list)
    for c in todo:
        if c.kind == "feed":
            feeds[c.feed].append(c)
    due = [c for c in months if _due(c, known.get(c.url), paper, args.refetch)]
    n["unchanged"] += len(months) - len(due)
    for k, child in enumerate(due):
        if over():
            return n, errors, len(due) - k + sum(len(p) for p in feeds.values())
        read(child)

    # QNA: newest first, 5,000 a page, so new items push every page along. Page 1 is always read;
    # the next page only if this one brought new articles, or if it has never been read whole.
    for name, pages in feeds.items():
        prev_new: int | None = 1
        for k, child in enumerate(sorted(pages, key=lambda c: c.page)):
            rec = known.get(child.url)
            if not (args.refetch or child.page == 1 or rec is None or not rec["complete"] or prev_new != 0):
                n["unchanged"] += 1
                prev_new = 0
                continue
            if over():
                return n, errors, len(pages) - k
            new = read(child)
            prev_new = 1 if new is None else new      # a page that failed: the next one is read to be safe
    return n, errors, 0


def cmd_load(args) -> int:
    site = _site(args)
    deadline = site.timer.monotonic() + args.max_minutes * 60 if args.max_minutes else None
    with Store(args.db) as store:
        with store.run("load") as info:
            known = store.sitemaps()
            notes, errors, left = [], [], 0
            for key in args.papers:
                paper = PAPERS[key]
                n, errs, still = _load_paper(site, store, paper, known, args, deadline)
                errors += errs
                notes.append(f"{paper.name}: {n['read']} sitemaps read, {n['unchanged']} unchanged, "
                             f"{n['failed']} failed, {n['gone']} gone, {n['disallowed']} disallowed, "
                             f"{n['new']} new articles")
                info["rows"] += n["new"]
                if still:
                    left = still
                    break
            info["note"] = "; ".join(notes)
            if left:
                info["status"] = "stopped"
                info["note"] += (f" -- stopped at the {args.max_minutes:g}-minute limit"
                                 + (f" with {left} sitemaps of {paper.name} still to read" if left > 0 else "")
                                 + "; run load again to continue")
            store.db.commit()
            if errors:   # what was read is kept; the run is an error and the next load tries the rest again
                raise SiteError(f"{len(errors)} sitemaps could not be read; first: {errors[0]}")
        print(info["note"])
        _table(store.stats())
    return 0


# ---- match ------------------------------------------------------------------

def cmd_match(args) -> int:
    with Store(args.db) as store:
        counts = store.paper_counts()
        if not counts:
            print("The database has no articles yet: run `load` first.", file=sys.stderr)
            return 2
        customers = load_customers(args.customers)
        site = None if args.no_search else _site(args)
        with store.run("match") as info:
            queries = [query_for(c) for c in customers]
            headlines = Headlines.build(store.articles(), {anchor(phrase(q)) for q in queries})
            rows, out = [], []
            s: Counter = Counter()
            first_error = None
            for c, q in zip(customers, queries):
                search = None
                generic = is_generic(q, headlines.common)
                if site is not None and not generic:
                    search = store.cached_search(q, args.search_max_age_days)
                    if search is not None:
                        s["cached"] += 1
                    elif s["in_a_row"] >= MAX_SEARCH_FAILURES:
                        s["failed"] += 1
                    else:
                        try:
                            search = parse.gt_search(site.get(fetch.gt_search_url(q)))
                            store.save_search(q, *search)
                            store.db.commit()
                            s["fetched"] += 1
                            s["in_a_row"] = 0
                        except (SiteError, ValueError) as e:
                            s["failed"] += 1
                            s["in_a_row"] = MAX_SEARCH_FAILURES if isinstance(e, Disallowed) else s["in_a_row"] + 1
                            first_error = first_error or str(e)
                row = match_one(c, headlines, search)
                rows.append(row)
                out.append(features(row, None if generic else fetch.gt_search_url(q)))
            info["rows"] = store.save_matches(rows, info["id"])
            found = sum(r["status"] == "found" for r in rows)
            generic = sum(r["status"] == "generic" for r in rows)
            info["note"] = (f"{found} of {len(rows)} customers with news, {generic} with a generic name (not counted); "
                            + ("Gulf Times search not done" if site is None else
                               f"Gulf Times search: {s['fetched']} fetched, {s['cached']} cached, "
                               f"{s['failed']} failed (left empty)")
                            + "; headlines: " + ", ".join(f"{p} {k}" for p, k in sorted(counts.items())))
            if headlines.peninsula_titles < 10000:
                info["note"] += (f"; only {headlines.peninsula_titles} Peninsula headlines: "
                                 "the generic-name rule has little to go on, load `peninsula`")
            if first_error:
                info["note"] += f"; first search error: {first_error}"
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
    ap = argparse.ArgumentParser(prog="python -m qatar_news", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    def network(p):
        p.add_argument("--delay", type=float, default=2.0,
                       help="least seconds between requests to one site (default 2; a longer Crawl-delay wins)")
        p.add_argument("--no-system-trust", action="store_true", help="do not use the Windows certificate store")

    p = sub.add_parser("load", help="read the papers' sitemaps into the headline index")
    p.add_argument("--papers", type=_papers, default=list(PAPERS),
                   help=f"comma-separated, from {','.join(PAPERS)} (default: all)")
    p.add_argument("--max-minutes", type=float, default=None,
                   help="stop cleanly after this long; the next load continues")
    p.add_argument("--refetch", action="store_true", help="fetch every sitemap again, not only new or changed ones")
    network(p)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("match", help="join a customer book (CSV: customer_id, name, cr_number[, search_name])")
    p.add_argument("customers")
    p.add_argument("--out", default="qatar_news_features.csv")
    p.add_argument("--no-search", action="store_true", help="headlines only, no Gulf Times search")
    p.add_argument("--search-max-age-days", type=float, default=30,
                   help="reuse a cached Gulf Times search this recent (default 30)")
    network(p)
    p.set_defaults(func=cmd_match)

    sub.add_parser("stats", help="what is in the database").set_defaults(func=cmd_stats)
    p = sub.add_parser("runs", help="the run log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)
    return ap


def main(argv=None, session=None, timer=None) -> int:
    args = build_parser().parse_args(argv)
    args.session = session
    args.timer = timer or time
    return args.func(args)
