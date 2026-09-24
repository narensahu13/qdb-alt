"""python -m icv <command>

The commands mirror qdb_alt/monaqasat so the two sources feel the same:

    crawl     sweep the supplier list, then read what moved
    refresh   one company, by CR number or name (the live fallback)
    status    where the crawl has got to
    stats     what is in the database
    match     join a customer book to the register
    export    a table as CSV
    pack      a copy to share, without raw pages or customer identities
    doctor    can this machine reach the portal, and does it still look the
              way this package expects
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from . import fetch, parse
from .features import build_client_months, client_rows
from .fetch import Portal, PortalError
from .match import (load_customers_report, match_suppliers, print_load_report,
                    summarise)
from .store import PACK_SKIP, SCHEMA, Store, today

DEFAULT_DB = "icv.db"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _deadline(args) -> float | None:
    hours = getattr(args, "max_hours", None)
    return time.monotonic() + hours * 3600 if hours else None


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _portal(args) -> Portal:
    # `session` is never set from the command line; the tests put a stand-in
    # portal there so the whole crawl runs offline.
    session = getattr(args, "session", None)
    if session is None and not getattr(args, "no_system_trust", False):
        fetch.use_system_trust_store()
    return Portal(delay=args.delay, timeout=args.timeout, session=session)


def _table(rows: Iterable[tuple[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    width = max(len(str(k)) for k, _ in rows)
    for k, v in rows:
        print(f"  {str(k).ljust(width)}  {v}")


# --------------------------------------------------------------------------
# crawl
# --------------------------------------------------------------------------

def cmd_crawl(args) -> int:
    store = Store(args.db)
    portal = _portal(args)
    deadline = _deadline(args)
    seen_on = today()
    run_id = store.start_run("crawl")
    new = changed = 0
    changed_ids: list[str] = []
    seen_ids: list[str] = []
    complete = False

    print("supplier list")
    try:
        cfg = portal.listing_config()

        def note(page: int, fresh: int, more: bool) -> None:
            print(f"  page {page}: {fresh} companies"
                  f"{'' if more else ' (last page)'}", flush=True)

        for row in portal.sweep(cfg, page_size=args.page_size, on_page=note):
            outcome = store.see_company(row, seen_on)
            seen_ids.append(row["id"])
            if outcome == "new":
                new += 1
                changed_ids.append(row["id"])
            elif outcome == "changed":
                changed += 1
                changed_ids.append(row["id"])
        complete = True
    except (PortalError, parse.PageShapeError) as exc:
        print(f"  stopped: {exc}", file=sys.stderr)
    store.conn.commit()

    gone = 0
    if complete:
        gone = store.close_missing(seen_ids, seen_on)
    else:
        print("  the sweep did not finish, so nothing is marked as gone",
              file=sys.stderr)
    store.bump(run_id, listed=len(seen_ids), changed=new + changed)
    store.conn.commit()
    print(f"  {len(seen_ids)} companies: {new} new, {changed} changed"
          + (f", {gone} gone" if gone else ""))

    details = added = 0
    if not args.listings_only:
        queue = store.needs_history(changed_ids,
                                    include_unread=not args.changed_only)
        if args.limit is not None:
            queue = queue[:args.limit]
        print(f"\nscore history: {len(queue)} to read")
        for i, cid in enumerate(queue, 1):
            if _expired(deadline):
                print(f"  time budget reached after {details} "
                      f"of {len(queue)}; the rest are read next run")
                break
            code = store.conn.execute(
                "SELECT status_code FROM listing_state WHERE company_id = ?"
                " AND to_date IS NULL", (cid,)).fetchone()
            try:
                html = portal.detail_page(cid, code["status_code"] if code else None)
                rows = portal.history(cid, html=html)
            except (PortalError, parse.PageShapeError) as exc:
                print(f"  {cid}: {exc}", file=sys.stderr)
                continue
            added += store.save_history(cid, rows, seen_on)
            if args.goods:
                try:
                    store.save_goods_services(
                        cid, portal.goods_and_services(cid, html), seen_on)
                except (PortalError, parse.PageShapeError) as exc:
                    print(f"  {cid}: goods/services: {exc}", file=sys.stderr)
            details += 1
            if i % 50 == 0 or i == len(queue):
                print(f"  {i}/{len(queue)} read, {added} new observations",
                      flush=True)
        store.bump(run_id, details=details, new_scores=added)
        store.conn.commit()

    row = store.finish_run(run_id, None if complete else "sweep incomplete")
    print(f"\nrun {row['id']}: {row['listed']} listed, {row['details']} "
          f"histories, {row['new_scores']} new observations, "
          f"{portal.requests_made} requests")
    if not args.listings_only and details == 0 and not args.changed_only:
        waiting = len(store.needs_history([]))
        if waiting:
            print(f"\nNothing was read from the detail pages, so {waiting} "
                  "company/companies still have no score history. Give the "
                  "run longer with --max-hours, or work through them in "
                  "batches with --limit 500.")
    store.close()
    return 0 if complete else 1


# --------------------------------------------------------------------------
# refresh -- the live fallback for one company
# --------------------------------------------------------------------------

def cmd_refresh(args) -> int:
    if not args.cr and not args.name:
        print("give --cr or --name", file=sys.stderr)
        return 2
    store = Store(args.db)
    portal = _portal(args)
    term = args.cr or args.name
    seen_on = today()
    try:
        found = portal.search(term)
    except (PortalError, parse.PageShapeError) as exc:
        print(f"could not reach the portal: {exc}", file=sys.stderr)
        store.close()
        return 1
    if not found:
        print(f"nothing on the ICV register for {term!r}. That is itself an "
              "answer: the company is not a certified supplier today.")
        store.close()
        return 1
    if len(found) > 1 and args.cr:
        found = [f for f in found if f.get("cr_number") == args.cr] or found
    for row in found[: args.limit or len(found)]:
        store.see_company(row, seen_on)
        try:
            rows = portal.history(row["id"], row.get("status_code"))
        except (PortalError, parse.PageShapeError) as exc:
            print(f"  {row['name']}: {exc}", file=sys.stderr)
            continue
        added = store.save_history(row["id"], rows, seen_on)
        print(f"{row['name']}  CR {row.get('cr_number')}  "
              f"score {row.get('total_score')}  {row.get('status')}  "
              f"expires {row.get('expiry_date')}")
        print(f"  {len(rows)} history rows ({added} new)")
        for h in rows[-args.show:] if args.show else rows:
            print(f"    {h['posted_date']}  {h['score']:>6}  "
                  f"{(h['change'] if h['change'] is not None else 0):>+6}  "
                  f"{h['reason']}")
    store.conn.commit()
    store.close()
    return 0


# --------------------------------------------------------------------------
# reading what is there
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    store = Store(args.db)
    last = store.last_run()
    if last is None:
        print("Nothing crawled yet. Run `crawl` first.")
        store.close()
        return 1
    _table([
        ("last run", f"{last['started_at']} -> {last['finished_at']}"),
        ("listed", last["listed"]),
        ("histories read", last["details"]),
        ("new observations", last["new_scores"]),
        ("note", last["note"] or "-"),
    ])
    waiting = len(store.needs_history([]))
    print()
    _table([("companies", store.stats()["companies"]),
            ("history not read yet", waiting)])
    if waiting:
        print("\n  `crawl --changed-only` keeps a run short; plain `crawl` "
              "works through the backlog.")
    store.close()
    return 0


def cmd_stats(args) -> int:
    store = Store(args.db)
    _table(store.stats().items())
    row = store.conn.execute(
        "SELECT MIN(posted_date) a, MAX(posted_date) b FROM score_observation"
    ).fetchone()
    if row and row["a"]:
        print()
        _table([("earliest observation", row["a"]),
                ("latest observation", row["b"])])
    reasons = store.conn.execute(
        "SELECT reason, COUNT(*) n FROM score_observation GROUP BY reason"
        " ORDER BY n DESC").fetchall()
    if reasons:
        print("\n  why the score moved")
        _table([(r["reason"] or "-", r["n"]) for r in reasons])
    store.close()
    return 0


def cmd_match(args) -> int:
    store = Store(args.db)
    customers, report = load_customers_report(args.customers, args.sheet)
    print_load_report(report)
    companies = store.companies()
    if not companies:
        print("No companies stored yet. Run `crawl` first.", file=sys.stderr)
        store.close()
        return 1
    matches = match_suppliers(customers, companies,
                              fuzzy_threshold=args.fuzzy,
                              translit_threshold=args.translit)
    store.save_matches(matches, None, replace=args.replace)
    _table(summarise(matches).items())

    raw_asof = getattr(args, "asof", None)
    as_of = date.fromisoformat(raw_asof) if raw_asof else date.today()
    names = {m["customer_id"]: m.get("customer_name") for m in matches}
    if args.out:
        rows = client_rows(store, as_of)
        for r in rows:
            r["client_name"] = names.get(r["client_id"])
        if rows:
            _write_csv(args.out, rows)
            print(f"\nwrote {args.out}: {len(rows)} row(s) as at {as_of}, "
                  "with the score on that date, what it was a year before, "
                  "how much of the move the company earned rather than was "
                  "given, and where it sits in its own sector")
            unknown = sum(1 for r in rows if not r["icv_standing_known"])
            if unknown:
                print(f"  {unknown} row(s) have no standing as at {as_of}: "
                      "status and expiry are only known from the first sweep "
                      "onwards, never for the past.")

    if getattr(args, "features", None):
        built = build_client_months(store, months=args.feature_months)
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM client_month ORDER BY client_id, as_of_month")]
        _write_csv(args.features, rows)
        print(f"wrote {args.features}: {built['rows']} client-months over "
              f"{built['clients']} client(s), point-in-time")
    store.close()
    return 0


def _write_csv(path: str, rows: list[dict]) -> None:
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


TABLES = {
    "companies": "SELECT * FROM company",
    "scores": "SELECT * FROM score_observation ORDER BY company_id, posted_at",
    "listing": "SELECT * FROM listing_state ORDER BY company_id, from_date",
    "matches": "SELECT * FROM match",
    "services": "SELECT * FROM company_service",
    "goods": "SELECT * FROM company_good",
}


def cmd_export(args) -> int:
    store = Store(args.db)
    rows = store.conn.execute(TABLES[args.table]).fetchall()
    if not rows:
        print(f"Nothing in {args.table}", file=sys.stderr)
        store.close()
        return 1
    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(rows[0].keys())
        w.writerows([tuple(r) for r in rows])
    print(f"wrote {args.out}: {len(rows)} rows")
    store.close()
    return 0


def cmd_pack(args) -> int:
    src = Store(args.db)
    dst_path = Path(args.out)
    if dst_path.exists():
        dst_path.unlink()
    dst = sqlite3.connect(str(dst_path))
    dst.executescript(SCHEMA)
    copied = {}
    for r in src.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"):
        name = r["name"]
        if name in PACK_SKIP or name.startswith("sqlite_"):
            continue
        rows = src.conn.execute(f"SELECT * FROM {name}").fetchall()
        if not rows:
            continue
        cols = ",".join(rows[0].keys())
        marks = ",".join("?" * len(rows[0].keys()))
        dst.executemany(f"INSERT OR REPLACE INTO {name}({cols}) "
                        f"VALUES({marks})", [tuple(x) for x in rows])
        copied[name] = len(rows)
    dst.commit()
    dst.close()
    src.close()
    _table(copied.items())
    print(f"\nwrote {dst_path} without {', '.join(PACK_SKIP)} -- no stored "
          "pages and no customer identities.")
    return 0


def cmd_doctor(args) -> int:
    print("1. system trust store")
    print("   " + ("in use" if fetch.use_system_trust_store()
                   else "not installed (pip install truststore) -- fine "
                        "unless TLS verification fails below"))
    portal = Portal(delay=args.delay, timeout=args.timeout)
    print("2. the supplier list page")
    try:
        html = portal.get_html(fetch.LISTING_PATH)
    except PortalError as exc:
        print(f"   cannot reach it: {exc}", file=sys.stderr)
        return 1
    print(f"   {len(html):,} bytes")
    if "entity-grid" not in html:
        print("   no entity grid in the page. What came back is probably a "
              "proxy block page or a sign-in page, not the portal.",
              file=sys.stderr)
        return 1
    print("3. verification token and grid configuration")
    try:
        cfg = parse.listing_config(html)
    except parse.PageShapeError as exc:
        print(f"   {exc}", file=sys.stderr)
        return 1
    print(f"   token {'yes' if portal.token else 'no'}, "
          f"sort {cfg.sort!r}")
    print("4. one page of the list")
    try:
        payload = portal.grid(cfg, page=1, page_size=3)
    except PortalError as exc:
        print(f"   {exc}", file=sys.stderr)
        return 1
    rows = [parse.company(r) for r in parse.records(payload)]
    rows = [r for r in rows if r]
    print(f"   {len(rows)} companies, more={parse.more(payload)}")
    for r in rows:
        print(f"   - {r['cr_number']}  {r['name'][:40]}  {r['total_score']}")
    if not rows:
        return 1
    print("5. one company's score history")
    try:
        hist = portal.history(rows[0]["id"], rows[0].get("status_code"))
    except (PortalError, parse.PageShapeError) as exc:
        print(f"   {exc}", file=sys.stderr)
        return 1
    print(f"   {len(hist)} observations, "
          f"{hist[0]['posted_date'] if hist else '-'} to "
          f"{hist[-1]['posted_date'] if hist else '-'}")
    print(f"\nall good -- {portal.requests_made} requests made")
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="icv", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--db", default=DEFAULT_DB)
        sp.add_argument("--delay", type=float, default=fetch.DELAY,
                        help="seconds between requests (default 1.5)")
        sp.add_argument("--timeout", type=int, default=fetch.TIMEOUT)
        sp.add_argument("--no-system-trust", action="store_true",
                        help="do not use the machine's certificate store")
        return sp

    c = common(sub.add_parser("crawl", help="sweep the list, then read what moved"))
    c.add_argument("--max-hours", type=float,
                   help="stop reading detail pages after this long")
    c.add_argument("--page-size", type=int, default=fetch.PAGE_SIZE)
    c.add_argument("--limit", type=int, help="at most this many detail pages")
    c.add_argument("--listings-only", action="store_true")
    c.add_argument("--changed-only", action="store_true",
                   help="only re-read companies whose listing moved, not the "
                        "backlog of ones never read")
    c.add_argument("--goods", action="store_true",
                   help="also read each company's goods and services (two "
                        "more requests each)")
    c.set_defaults(func=cmd_crawl)

    r = common(sub.add_parser("refresh", help="one company, live"))
    r.add_argument("--cr")
    r.add_argument("--name")
    r.add_argument("--limit", type=int, default=1)
    r.add_argument("--show", type=int, default=0,
                   help="print only the last N observations")
    r.set_defaults(func=cmd_refresh)

    s = sub.add_parser("status"); s.add_argument("--db", default=DEFAULT_DB)
    s.set_defaults(func=cmd_status)

    t = sub.add_parser("stats"); t.add_argument("--db", default=DEFAULT_DB)
    t.set_defaults(func=cmd_stats)

    m = sub.add_parser("match", help="join a customer book to the register")
    m.add_argument("--db", default=DEFAULT_DB)
    m.add_argument("--customers", required=True)
    m.add_argument("--sheet")
    m.add_argument("--out")
    m.add_argument("--replace", action="store_true")
    m.add_argument("--fuzzy", type=float, default=0.92)
    m.add_argument("--translit", type=float, default=0.90)
    m.add_argument("--asof", help="read every score and standing as at this "
                                  "date (YYYY-MM-DD), not as at today")
    m.add_argument("--features",
                   help="also write the client-by-month feature table here")
    m.add_argument("--feature-months", type=int, default=60)
    m.set_defaults(func=cmd_match)

    e = sub.add_parser("export"); e.add_argument("--db", default=DEFAULT_DB)
    e.add_argument("--table", choices=sorted(TABLES), default="companies")
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export)

    k = sub.add_parser("pack"); k.add_argument("--db", default=DEFAULT_DB)
    k.add_argument("--out", default="icv.slim.db")
    k.set_defaults(func=cmd_pack)

    d = sub.add_parser("doctor", help="can this machine read the portal")
    d.add_argument("--delay", type=float, default=fetch.DELAY)
    d.add_argument("--timeout", type=int, default=fetch.TIMEOUT)
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
