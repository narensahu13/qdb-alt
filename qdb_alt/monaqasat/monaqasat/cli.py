"""Command line.

    python -m monaqasat doctor                       why won't it connect?
    python -m monaqasat probe                        size every section
    python -m monaqasat harvest --max-hours 6        unattended, resumable
    python -m monaqasat crawl --kind all             bring every section up to date
    python -m monaqasat crawl --kind all --full      every listing page, now
    python -m monaqasat companies                    the classified register
    python -m monaqasat status                       progress and retries
    python -m monaqasat history --tender 659460      one tender's lifecycle
    python -m monaqasat parse                        re-parse stored HTML
    python -m monaqasat match --customers cust.csv   join to your book
    python -m monaqasat analyse                      bid-to-win conversion
    python -m monaqasat features                     CR-by-month model table
    python -m monaqasat profile --customer C001
    python -m monaqasat stats
    python -m monaqasat export --table awards --out awards.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .fetch import Blocked, Fetcher, PageTimeout
from .features import build_client_months
from .match import (FUZZY_THRESHOLD, TRANSLIT_THRESHOLD, load_counterparties,
                    load_customers_report, match_classified, match_customers,
                    match_parties, parties, print_counterparty_report,
                    print_load_report, summarise)
from .normalize import cr_root
from .parse import (BID_KINDS, COMPANY_KINDS, LISTING_KINDS, TENDER_KINDS,
                    kind_from_url, kind_path, last_listing_page, looks_rejected,
                    parse_company_list, parse_detail, parse_listing,
                    parse_tender_details)
from .store import Store, _now, companies_ref

DEFAULT_DB = "monaqasat.db"
COMPANY_LIST = "/ClassifiedCompaniesProfilesList"


def _pages(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _kinds(spec: str) -> list[str]:
    if spec == "all":
        return list(LISTING_KINDS)
    if spec == "tenders":
        return TENDER_KINDS
    if spec == "bids":
        return BID_KINDS
    if spec == "companies":          # only the states that name companies
        return COMPANY_KINDS
    out = [k.strip() for k in spec.split(",") if k.strip()]
    bad = [k for k in out if k not in LISTING_KINDS]
    if bad:
        raise SystemExit(f"unknown section(s): {', '.join(bad)}\n"
                         f"choose from: {', '.join(LISTING_KINDS)}"
                         " -- or all | tenders | bids | companies")
    return out


# --------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    from .diagnose import run
    return run(args.host)


def cmd_probe(args) -> int:
    f = Fetcher(delay=args.delay)
    try:
        robots = f.check_robots()
    except Exception as exc:                       # noqa: BLE001
        print(f"robots.txt: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"TLS: {f.tls_mode}")
    print("robots.txt: " + " | ".join(
        l.strip() for l in robots.strip().splitlines() if l.strip()))

    print(f"\n{'section':<18} {'family':<7} {'state':<10} {'pages':>6} "
          f"{'~items':>8}  companies")
    print("-" * 66)
    total = 0
    for kind in _kinds(args.kind):
        spec = LISTING_KINDS[kind]
        try:
            html, _ = f.get(f.listing_path(kind_path(kind), 1))
        except Blocked as exc:
            print(f"{kind:<18} blocked: {exc}", file=sys.stderr)
            continue
        rows = parse_listing(html, kind)
        last = last_listing_page(html, kind) or (1 if rows else 0)
        approx = last * len(rows) if rows else 0
        total += approx
        print(f"{kind:<18} {spec['family']:<7} {spec['state']:<10} "
              f"{last:>6} {approx:>8,}  "
              f"{'yes' if spec['has_companies'] else '-'}")

    if args.kind in {"all", "tenders", "bids"}:
        try:
            html, _ = f.get(f"{COMPANY_LIST}/1")
            rows = parse_company_list(html)
            last = last_listing_page(html, "ClassifiedCompaniesProfilesList")
            print(f"{'classified':<18} {'-':<7} {'register':<10} "
                  f"{last or 0:>6} {(last or 0) * len(rows):>8,}  -")
        except Blocked:
            pass

    print("-" * 66)
    print(f"{'':<37}{total:>8,} tender/bid records")
    return 0


# --------------------------------------------------------------------------
# crawling
#
# Page numbers are not stable, so progress is tracked by tender id and
# section, never by page number. How each section is kept current depends on
# how the site orders it -- checked on the live site, Sept 2026:
#
#   Awarded      newest award date first. A head scan from page 1 finds new
#                awards; it stops only once it is past the newest award date
#                already held (minus a lookback, for awards published with an
#                earlier date) and has seen two pages with nothing new.
#   Cancelled    in no date order: a newly cancelled tender can land on any
#                page. Head scan plus a rolling full pass.
#   everything   Available (publish date), Closed (publish date, NOT closing
#   else         date), Technically / Financially Opened (neither), Future,
#                and all the Bids sections. All small (about 275 pages in
#                total), and a tender can enter any of them deep in the list,
#                so every run reads them end to end. That full read is also
#                what lets a run notice that a tender has LEFT a section.
#
# The rolling pass re-reads Awarded and Cancelled end to end, so nothing a
# head scan misses stays missed. How much of it a run does is sized from how
# long it has been since the last run: the pass should finish within
# --sweep-days whether the tool runs nightly (a slice a night) or once a
# month (the whole section in that one run). --roll-pages fixes the slice
# instead, for anyone who wants to cap what a single run costs.
#
# Listing pages are cheap -- one request covers twenty tenders. Detail pages
# are the cost. They are fetched after every listing has been read, so each
# tender's state is settled first (a tender listed under both Technically
# and Financially Opened is fetched once, as financial), and only when they
# can say something new.
# --------------------------------------------------------------------------

MARGIN = 3              # overlap when resuming, since pages drift
MAX_CONSECUTIVE_FAILS = 5

ROLLING = {"awarded", "cancelled"}
DATE_ORDERED = {"awarded": "awarded_date", "bids-awarded": "awarded_date"}

LOOKBACK_DAYS = 14
ROLL_PAGES = 100
SWEEP_DAYS = 7


# A run with a time budget splits it: the listing pass gets this share, the
# detail pages get the rest. Without it, a first load spends every run on
# listing pages -- 1,212 of them for Awarded -- and the database holds
# tenders with no winners, no prices and nothing to match a customer to for
# days. If the listings finish early the details get the whole budget, and
# if the details finish early the listings carry on with what is left.
LISTING_SHARE = 0.6


def _expired(args, phase: str = "") -> bool:
    deadline = getattr(args, "deadline", None)
    if phase == "listings":
        listing = getattr(args, "listing_deadline", None)
        if listing is not None:
            deadline = listing if deadline is None else min(deadline, listing)
    return deadline is not None and time.monotonic() >= deadline


def _set_deadline(args) -> None:
    hours = getattr(args, "max_hours", None)
    if hours and not getattr(args, "deadline", None):
        args.deadline = time.monotonic() + hours * 3600
    deadline = getattr(args, "deadline", None)
    if deadline is not None and getattr(args, "listing_deadline", None) is None:
        left = max(deadline - time.monotonic(), 0.0)
        args.listing_deadline = time.monotonic() + left * LISTING_SHARE


def _opt(args, name, default):
    value = getattr(args, name, None)
    return default if value is None else value


def _slice_pages(store, args, last: int, *, flag: str, period: str,
                 period_default: float, floor: int, command: str,
                 run_id: int | None = None) -> int:
    """How many pages of a rolling pass to read this run.

    Enough that the pass finishes within its period at the frequency the
    tool is actually being run: a nightly run takes a seventh of the
    section, a run a month later takes all of it. An explicit --roll-pages
    or --register-pages overrides this and caps the run instead.
    """
    explicit = getattr(args, flag, None)
    if explicit:
        return int(explicit)
    days = _opt(args, period, period_default) or period_default
    gap = max(store.days_since_last_run(command, run_id), 1.0)
    share = 1.0 if days <= 0 else min(1.0, gap / days)
    return max(floor, math.ceil(last * share))


def _policy(kind: str, args) -> str:
    if args.pages:
        return "pages"
    if args.full:
        return "full"
    if getattr(args, "quick", False):
        return "head"
    return "roll" if kind in ROLLING else "sweep"


def _older_than(stamp: str | None, days: float) -> bool:
    if not stamp:
        return True
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - then >= timedelta(days=days)


def _range_for(args, last: int) -> list[int]:
    if args.pages:
        return [p for p in _pages(args.pages) if 1 <= p <= last]
    return list(range(1, last + 1))


def _fetch_listing(store, f, kind, page, run_id):
    """Fetch, store and parse one listing page. (None, None) on failure."""
    rel = f.listing_path(kind_path(kind), page)
    url = f"{f.base}{rel}"
    try:
        html, status = f.get(rel)
    except Blocked as exc:
        store.record_failure(kind, str(page), url, str(exc))
        store.bump(run_id, failures=1)
        why = "timed out" if isinstance(exc, PageTimeout) else "failed"
        print(f"  {kind} page {page}: {why}, skipped -- retried next run",
              flush=True)
        return None, None
    store.save_page(url, "listing", html, status=status, page=page)
    store.clear_failure(kind, str(page))
    store.bump(run_id, listing_pages=1)
    return html, parse_listing(html, kind)


def _absorb(store, kind, rows, url, run_id) -> int:
    """Record what a listing page showed. Returns how many were new here.

    The listing's own state is not written to the tender: the state is
    re-derived from every section the tender is listed in now.
    """
    spec = LISTING_KINDS[kind]
    new = moved = 0
    for r in rows:
        fields = {k: v for k, v in r.items() if k != "state"}
        store.upsert_tender(fields, source_url=url)
        new += store.record_sighting(r["tender_id"], kind, spec["family"],
                                     spec["state"], run_id)
        change = store.refresh_state(r["tender_id"])
        moved += bool(change and change[0] is not None)
    store.bump(run_id, new_tenders=new, state_changes=moved)
    store.commit()
    return new


def _walk_listings(store, f, kind, args, run_id) -> str:
    """Read listing pages for one section. Returns a one-line outcome."""
    sec = store.section(kind)
    policy = _policy(kind, args)
    stop_after = _opt(args, "stop_after", 2)

    date_field = DATE_ORDERED.get(kind)
    floor = None
    if date_field and policy in ("head", "roll"):
        mark = store.watermark(kind, date_field)
        if mark:
            floor = (date.fromisoformat(mark) - timedelta(
                days=_opt(args, "lookback_days", LOOKBACK_DAYS))).isoformat()

    failed_before = sorted({int(r["ref"]) for r in store.failures(kind)
                            if str(r["ref"]).isdigit()})

    rel1 = f.listing_path(kind_path(kind), 1)
    html, rows = _fetch_listing(store, f, kind, 1, run_id)
    if html is None:
        return "could not read page 1 -- section skipped"
    last = last_listing_page(html, kind) or 1
    per_page = max(1, len(rows))
    store.update_section(kind, last_page_seen=last)
    in_range = _range_for(args, last)
    lo, hi = (min(in_range), max(in_range)) if in_range else (1, 0)

    fresh_total = (_absorb(store, kind, rows, f"{f.base}{rel1}", run_id)
                   if 1 in in_range else 0)
    deepest = max(sec["deepest_page"], 1)
    fails = 0
    visited = {1}

    def visit(page):
        """-> (new here, rows), or (None, None) if the page failed."""
        nonlocal deepest, fails
        visited.add(page)
        html, rows = _fetch_listing(store, f, kind, page, run_id)
        if html is None:
            fails += 1
            return None, None
        fails = 0
        deepest = max(deepest, page)
        store.update_section(kind, deepest_page=deepest)
        url = f"{f.base}{f.listing_path(kind_path(kind), page)}"
        return _absorb(store, kind, rows, url, run_id), rows

    # -- full read: every page, then mark what is no longer listed ---------
    if policy in ("sweep", "full"):
        clean = True
        for page in range(2, last + 1):
            if _expired(args, "listings"):
                return (f"time budget reached at page {page - 1} of {last}"
                        " -- not a full read, so nothing marked as gone")
            n, _ = visit(page)
            if n is None:
                clean = False
            else:
                fresh_total += n
            if fails >= MAX_CONSECUTIVE_FAILS:
                return f"stopped after {fails} failed pages in a row"
        store.clear_failures_beyond(kind, last)
        news = f"; {fresh_total} new here" if fresh_total else ""
        if not clean:
            store.update_section(kind, reached_end=1, deepest_page=last)
            return (f"read {last} pages{news}; {len(store.failures(kind))} "
                    "failed and will be retried -- not a clean read, so "
                    "nothing marked as gone")
        gone = store.mark_unseen(kind, run_id)
        store.update_section(kind, reached_end=1, backfill_complete=1,
                             deepest_page=last, last_full_pass=_now(),
                             roll_next=None)
        return (f"read all {last} pages{news}"
                + (f"; {len(gone)} no longer listed here" if gone else ""))

    # -- head scan, first-load resume, gap repair, rolling pass ------------
    def settle() -> bool:
        """Record whether the section is read to the end, and whether it is
        complete -- read to the end with no page left failed."""
        was = bool(store.section(kind)["reached_end"])
        reached = was or (hi >= last and deepest >= last)
        if reached:
            store.update_section(kind, reached_end=1, deepest_page=last)
        clean = not store.failures(kind)
        store.update_section(kind, backfill_complete=int(reached and clean))
        if reached and clean and not was and policy != "pages":
            store.update_section(kind, last_full_pass=_now())
        return reached and clean

    def budget_out() -> str:
        settle()
        return f"time budget reached at page {deepest} of {last}"

    def past_floor(rows) -> bool:
        """True once a page reaches award dates older than the floor."""
        if floor is None or not rows:
            return True
        dates = [r.get(date_field) for r in rows if r.get(date_field)]
        return not dates or min(dates) < floor

    pages = [p for p in in_range if p != 1]

    # 1. newest pages until caught up: two pages in a row with nothing new
    #    and, where the section is ordered by date, past the date floor.
    streak = 0 if fresh_total else 1
    below = past_floor(rows)
    caught_up = 1
    for page in pages:
        if _expired(args, "listings"):
            return budget_out()
        if streak >= stop_after and below:
            break
        n, prow = visit(page)
        caught_up = page
        if fails >= MAX_CONSECUTIVE_FAILS:
            return f"stopped after {fails} failed pages in a row"
        if n is None:
            continue
        fresh_total += n
        streak = streak + 1 if n == 0 else 0
        below = past_floor(prow)
    outcome = f"caught up at page {caught_up}"
    if fresh_total:
        outcome += f"; {fresh_total} new here"

    shift = fresh_total // per_page

    # 2. finish an interrupted first load
    if not sec["reached_end"]:
        resume_from = max(caught_up + 1, sec["deepest_page"] + shift - MARGIN)
        rest = [p for p in pages if p >= resume_from and p not in visited]
        if rest:
            print(f"  {kind}: first load incomplete -- continuing from page "
                  f"{rest[0]} to {rest[-1]}", flush=True)
        for page in rest:
            if _expired(args, "listings"):
                return budget_out()
            visit(page)
            if fails >= MAX_CONSECUTIVE_FAILS:
                return f"stopped after {fails} failed pages in a row"
            if page % 25 == 0:
                print(f"    {kind} page {page}/{rest[-1]}", flush=True)

    # 3. revisit gaps left by earlier failures, shifted by however far new
    #    tenders have pushed the lost page down since
    healed = 0
    for old in failed_before:
        window = [p for p in range(old + shift - MARGIN, old + shift + MARGIN + 1)
                  if lo <= p <= hi and p <= last]
        todo = [p for p in window if p not in visited]
        ok = True
        for page in todo:
            if _expired(args, "listings"):
                return budget_out()
            if visit(page)[0] is None:
                ok = False
            if fails >= MAX_CONSECUTIVE_FAILS:
                return f"stopped after {fails} failed pages in a row"
        if ok and window:
            store.clear_failure(kind, str(old))
            healed += 1
    if healed:
        outcome += f"; recovered {healed} previously failed page(s)"

    first_load_done = settle()

    # 4. rolling full pass (Awarded, Cancelled), a slice per run
    if policy == "roll" and store.section(kind)["reached_end"]:
        s = store.section(kind)
        nxt = s["roll_next"]
        if nxt is not None or _older_than(s["last_full_pass"],
                                          _opt(args, "sweep_days", SWEEP_DAYS)):
            # Resume where the last slice ended, moved down by however many
            # pages of new awards arrived since; one page of overlap covers
            # the rounding.
            start = 2 if nxt is None else max(2, nxt + shift - 1)
            slice_pages = _slice_pages(
                store, args, last, flag="roll_pages", period="sweep_days",
                period_default=SWEEP_DAYS, floor=ROLL_PAGES, command="crawl",
                run_id=run_id)
            end = min(last, start + slice_pages - 1)
            for page in range(start, end + 1):
                if page in visited:
                    continue
                if _expired(args, "listings"):
                    store.update_section(kind, roll_next=page)
                    return outcome + f"; rolling pass paused at page {page}"
                visit(page)
                if fails >= MAX_CONSECUTIVE_FAILS:
                    store.update_section(kind, roll_next=page)
                    return f"stopped after {fails} failed pages in a row"
            if end >= last:
                store.update_section(kind, roll_next=None,
                                     last_full_pass=_now())
                outcome += f"; rolling pass finished (pages {start}-{end})"
            else:
                store.update_section(kind, roll_next=end + 1)
                outcome += f"; rolling pass at page {end} of {last}"

    if first_load_done:
        if not sec["backfill_complete"]:
            outcome += "; first load complete"
    elif not store.section(kind)["reached_end"]:
        outcome += f"; first load reached page {deepest} of {last}"
    elif store.failures(kind):
        outcome += f"; {len(store.failures(kind))} page(s) still to retry"
    return outcome


def _fetch_details(store, f, kinds, args, run_id) -> str:
    """Detail pages for every tender in these sections that needs one."""
    jobs = store.detail_jobs(kinds, tender_details=not args.no_details)
    if not jobs:
        return "up to date"
    if args.limit_details:
        jobs = jobs[:args.limit_details]

    print(f"  {len(jobs)} detail pages to fetch", flush=True)
    done = fails = missing = 0
    for n, (ptype, tid, state) in enumerate(jobs, 1):
        if _expired(args):
            return f"{done} fetched, then time budget reached"
        rel = (f"/TendersOnlineServices/TenderDetails/{tid}"
               if ptype == "tender_details" else f.detail_path(tid))
        url = f"{f.base}{rel}"
        ref = companies_ref(tid, state) if ptype == "companies" else tid
        try:
            html, status = f.get(rel)
        except Blocked as exc:
            store.record_failure(ptype, ref, url, str(exc))
            store.bump(run_id, failures=1)
            fails += 1
            if fails >= MAX_CONSECUTIVE_FAILS:
                return (f"{done} fetched, then stopped after {fails} "
                        "network failures in a row")
            continue
        fails = 0          # the server answered; whatever it said, it is up

        if ptype == "tender_details":
            parsed = parse_tender_details(html, tid)
            if not parsed.get("found"):
                # The site answers an unknown id with its home page. Not a
                # network failure: it must not stop the queue.
                store.record_missing(ptype, tid, url)
                missing += 1
                continue
            store.save_page(url, "tender_details", html, status=status,
                            tender_id=tid)
            store.upsert_tender(parsed["tender"], source_url=url)
            store.replace_tender_activities(tid, parsed["activities"])
            store.mark_detail(tid, ptype, state)
            store.clear_failure(ptype, ref)
            store.clear_failure(f"missing:{ptype}", tid)
        else:
            parsed = parse_detail(html, tid)
            if not parsed.get("found"):
                store.record_missing(ptype, tid, url)
                missing += 1
                continue
            store.save_page(url, "detail", html, status=status, tender_id=tid)
            store.upsert_tender(parsed["tender"], source_url=url)
            store.replace_companies(tid, parsed["companies"])
            if state == "awarded" and not any(
                    c["role"] == "awarded" for c in parsed["companies"]):
                # Listed as awarded, but the page does not name a winner
                # yet. Keep what it shows, and read it again next run
                # (up to five times) instead of marking it done.
                store.record_failure(ptype, ref, url,
                                     "listed as awarded; no winner on the page yet")
            else:
                store.mark_detail(tid, ptype, state)
                store.clear_failure(ptype, ref)
                store.clear_failure(f"missing:{ptype}", tid)
        store.bump(run_id, details=1)
        store.commit()
        done += 1
        if n % 50 == 0:
            print(f"    {n}/{len(jobs)}", flush=True)
    note = f"{done} fetched"
    if missing:
        note += (f"; {missing} answered with the home page (tried again "
                 "in a week, up to 3 times)")
    return note


def _print_run(row) -> None:
    secs = ""
    try:
        a = datetime.fromisoformat(row["started_at"])
        b = datetime.fromisoformat(row["finished_at"])
        mins = (b - a).total_seconds() / 60
        secs = f" in {mins:.1f} min"
    except Exception:                               # noqa: BLE001, S110
        pass
    print(f"\nrun {row['run_id']} ({row['mode']}){secs}")
    print(f"  listing pages read   {row['listing_pages']}")
    print(f"  new to a section     {row['new_tenders']}")
    print(f"  state changes        {row['state_changes']}")
    print(f"  detail pages fetched {row['details']}")
    if row["companies"]:
        print(f"  companies updated    {row['companies']}")
    if row["failures"]:
        print(f"  failed, will retry   {row['failures']}")


def _mode(args) -> str:
    if args.pages:
        return f"pages {args.pages}"
    if args.full:
        return "full"
    return "quick" if getattr(args, "quick", False) else "update"


def cmd_crawl(args) -> int:
    _set_deadline(args)
    store = Store(args.db)
    f = Fetcher(delay=args.delay, read_timeout=args.timeout)
    try:
        f.check_robots()
        print(f"TLS: {f.tls_mode}")
    except Exception as exc:                       # noqa: BLE001
        print(f"{exc}", file=sys.stderr)
        return 1

    kinds = _kinds(args.kind)
    run_id = store.start_run("crawl", args.kind, _mode(args))

    # 1. every listing first, so each tender's state is settled. On a run
    #    with a time budget the listings get LISTING_SHARE of it, so that a
    #    first load -- 1,212 pages for Awarded alone -- cannot use up every
    #    run and leave the store with tenders but no winners.
    def walk_listings() -> bool:
        cut = False
        for i, kind in enumerate(kinds):
            if i and _expired(args, "listings"):
                print("\n  time budget for listings reached -- the rest is "
                      "read on the next run")
                cut = True
                break
            print(f"\n{kind}")
            outcome = _walk_listings(store, f, kind, args, run_id)
            print(f"  listings: {outcome}")
            cut = cut or "time budget" in outcome
        return cut

    cut_short = walk_listings()

    # 2. then the detail pages, most valuable first
    if not args.listings_only and not _expired(args):
        print("\ndetail pages")
        print(f"  {_fetch_details(store, f, kinds, args, run_id)}")

    # 3. if the details ran out of work before the clock did, spend what is
    #    left finishing the listings the split cut short.
    if cut_short and not _expired(args):
        args.listing_deadline = None
        print("\nback to the listings with the time left over")
        walk_listings()

    row = store.finish_run(run_id)
    _print_run(row)
    if not row["details"] and not args.listings_only:
        waiting = len(store.detail_jobs(
            kinds, tender_details=not args.no_details))
        if waiting:
            print(f"\nNo detail pages were read this run, so there are still "
                  f"no winners or prices to match against. {waiting} page(s) "
                  "are queued. Give the run longer (--max-hours 1), or keep "
                  "it inside the listing pages already held so the time goes "
                  "on details:\n"
                  f"    python -m monaqasat crawl --kind {args.kind} "
                  f"--pages 1-{store.section(kinds[0])['deepest_page'] or 1} "
                  "--max-hours 0.25")
    left = store.left_every_list()
    if left:
        print(f"\n{len(left)} tender(s) have left every section read so far "
              "and not yet been seen as awarded or cancelled. The rolling "
              "pass over those two sections will place them "
              "(`history --tender ID` shows one).")
    pending = store.failures()
    if pending:
        print(f"\n{len(pending)} pages are waiting to be retried "
              "(see `status`).")
    store.close()
    return 0


REGISTER_PAGES = 20
REGISTER_DAYS = 30


def cmd_companies(args) -> int:
    """The classified register.

    Unlike the tender listings, this one is oldest-first: page 1 holds the
    earliest profile numbers and new companies land on the last page. So an
    update reads the tail for new companies, plus a slice of a rolling pass
    over the whole register (--register-pages per run, a new pass every
    --register-days) that refreshes evaluations, certificate dates and
    activities, and notices companies that have been removed. --full does a
    whole pass in one go.

    A company missing from two complete passes in a row is marked delisted
    (delisted_at). Two, because a company removed while a pass is under way
    shifts the rest up a place and one can slip across a page boundary.
    """
    _set_deadline(args)
    store = Store(args.db)
    f = Fetcher(delay=args.delay, read_timeout=args.timeout)
    try:
        f.check_robots()
        print(f"TLS: {f.tls_mode}")
    except Exception as exc:                       # noqa: BLE001
        print(f"{exc}", file=sys.stderr)
        return 1

    kind = "classified"
    sec = store.section(kind)
    try:
        html, _ = f.get(f"{COMPANY_LIST}/1")
    except Blocked as exc:
        print(f"could not read the register: {exc}", file=sys.stderr)
        return 1
    last = last_listing_page(html, "ClassifiedCompaniesProfilesList") or 1

    retry = sorted({int(r["ref"]) for r in store.failures(kind)
                    if str(r["ref"]).isdigit()})
    pass_id = sec["pass_id"]
    roll_next = sec["roll_next"]
    budget = _slice_pages(
        store, args, last, flag="register_pages", period="register_days",
        period_default=REGISTER_DAYS, floor=REGISTER_PAGES,
        command="companies")

    if args.pages:
        pages = [p for p in _pages(args.pages) if 1 <= p <= last]
        mode = "pages"
        pass_pages: list[int] = []
    elif args.full:
        pass_id += 1
        pass_pages = list(range(1, last + 1))
        pages = pass_pages
        mode = "full"
    elif not sec["reached_end"]:
        # The first load is the first pass. The register grows at the end,
        # so a resume needs no safety margin -- only the last page read, in
        # case a deleted company pulled a neighbour back across the boundary.
        if pass_id == 0:
            pass_id = 1
        start = max(1, sec["deepest_page"])
        pass_pages = list(range(start, last + 1))
        pages = sorted(set(pass_pages) | set(retry))
        mode = "first load" if start == 1 else f"resume from page {start}"
    else:
        prev_last = sec["last_page_seen"] or last
        tail = set(range(max(1, prev_last - 1), last + 1))
        if roll_next is None and _older_than(
                sec["last_full_pass"], _opt(args, "register_days", REGISTER_DAYS)):
            pass_id += 1
            roll_next = 1
        pass_pages = (list(range(roll_next, min(last, roll_next + budget - 1) + 1))
                      if roll_next is not None and roll_next <= last else [])
        pages = sorted(tail | set(retry) | set(pass_pages))
        mode = "update" + (f", refresh pass {pass_id} from page {roll_next}"
                           if pass_pages else "")

    print(f"register: {last} pages -- {mode}, {len(pages)} to fetch"
          + (f" (including {len(retry)} retries)" if retry and mode != "full"
             else ""))
    run_id = store.start_run("companies", "classified", mode)
    store.update_section(kind, last_page_seen=last, pass_id=pass_id)

    deepest = sec["deepest_page"]
    fails = total = 0
    stopped_at = None
    for page in pages:
        if _expired(args):
            stopped_at = page
            print(f"  time budget reached at page {page} of {last}",
                  flush=True)
            break
        rel = f"{COMPANY_LIST}/{page}"
        url = f"{f.base}{rel}"
        try:
            html, status = f.get(rel)
        except Blocked as exc:
            store.record_failure(kind, str(page), url, str(exc))
            store.bump(run_id, failures=1)
            why = "timed out" if isinstance(exc, PageTimeout) else "failed"
            print(f"  page {page}: {why}, skipped -- retried next run",
                  flush=True)
            fails += 1
            if fails >= MAX_CONSECUTIVE_FAILS:
                stopped_at = page
                print(f"\nstopping: {fails} pages in a row failed. The site "
                      "may be slow or refusing; try later.", file=sys.stderr)
                break
            continue
        fails = 0
        store.save_page(url, "classified", html, status=status, page=page)
        rows = parse_company_list(html)
        for r in rows:
            store.upsert_company(r, page=page, pass_id=pass_id)
        store.mark_page(kind, page, len(rows))
        store.clear_failure(kind, str(page))
        store.bump(run_id, listing_pages=1, companies=len(rows))
        store.commit()
        total += len(rows)
        deepest = max(deepest, page)
        store.update_section(kind, deepest_page=deepest)
        if page % 10 == 0 or page == pages[-1]:
            print(f"  page {page}/{last}  {total} companies", flush=True)

    # Where the pass stands. It completes only when every page of it has
    # been read and nothing is left to retry.
    if pass_pages:
        if stopped_at is not None and stopped_at <= pass_pages[-1]:
            next_page = max(pass_pages[0], stopped_at)
        else:
            next_page = pass_pages[-1] + 1
        store.update_section(kind, roll_next=next_page)
    s = store.section(kind)
    if deepest >= last:
        store.update_section(kind, reached_end=1)
    if (s["roll_next"] is not None and s["roll_next"] > last
            and not store.failures(kind) and store.section(kind)["reached_end"]):
        missed, delisted = store.mark_unlisted(s["pass_id"])
        store.update_section(kind, roll_next=None, last_full_pass=_now())
        print(f"  pass {s['pass_id']} complete"
              + (f"; {delisted} company(ies) no longer on the register"
                 if delisted else ""))
    store.update_section(
        kind, backfill_complete=int(bool(store.section(kind)["reached_end"])
                                    and not store.failures(kind)))

    _print_run(store.finish_run(run_id))
    left = store.failures(kind)
    if left:
        print(f"\n{len(left)} register pages failed: "
              + ", ".join(r["ref"] for r in left[:15])
              + (" ..." if len(left) > 15 else "")
              + "\nRe-run `companies` to retry just those.")
    store.close()
    return 0


def cmd_status(args) -> int:
    """Where each section stands, what is waiting, and recent runs."""
    store = Store(args.db)
    rows = store.conn.execute(
        "SELECT s.*,"
        " (SELECT COUNT(*) FROM sighting g WHERE g.kind=s.kind"
        "   AND g.gone_at IS NULL) listed,"
        " (SELECT COUNT(*) FROM sighting g WHERE g.kind=s.kind"
        "   AND g.gone_at IS NOT NULL) gone"
        " FROM section_state s ORDER BY s.kind").fetchall()
    if rows:
        print(f"{'section':<16} {'listed':>7} {'left':>6} {'pages':>6}  "
              f"{'first load':<11} {'last full read':<17} rolling pass")
        print("-" * 84)
        for r in rows:
            listed, gone = r["listed"], r["gone"]
            if r["kind"] == "classified":
                listed = store.conn.execute(
                    "SELECT COUNT(*) FROM classified_company"
                    " WHERE delisted_at IS NULL").fetchone()[0]
                gone = store.conn.execute(
                    "SELECT COUNT(*) FROM classified_company"
                    " WHERE delisted_at IS NOT NULL").fetchone()[0]
            roll = (f"at page {r['roll_next']}" if r["roll_next"] is not None
                    else "-")
            print(f"{r['kind']:<16} {listed:>7} {gone:>6} "
                  f"{r['last_page_seen'] or '-':>6}  "
                  f"{'complete' if r['backfill_complete'] else 'in progress':<11} "
                  f"{(r['last_full_pass'] or '-')[:16]:<17} {roll}")
    else:
        print("Nothing crawled yet.")

    left = store.left_every_list()
    if left:
        print(f"\n{len(left)} tender(s) have left every section read so far and "
              "are waiting to be seen as awarded or cancelled, e.g. "
              + ", ".join(left[:5]))

    pend = store.conn.execute(
        "SELECT kind, COUNT(*) n, MAX(attempts) a FROM failed_page"
        " GROUP BY kind ORDER BY kind").fetchall()
    if pend:
        print("\nwaiting to retry")
        for r in pend:
            print(f"  {r['kind']:<32} {r['n']:>5} pages "
                  f"(up to {r['a']} attempts)")

    runs = store.runs(args.runs)
    if runs:
        print(f"\nlast {len(runs)} runs")
        for r in runs:
            print(f"  #{r['run_id']:<4} {(r['started_at'] or '')[:16]}  "
                  f"{r['command']:<9} {r['target']:<12} {r['mode'][:22]:<22} "
                  f"new {r['new_tenders']:>5}  details {r['details']:>5}  "
                  f"failed {r['failures']:>3}")
    store.close()
    return 0


def cmd_history(args) -> int:
    """Everything recorded about one tender -- how the crawl saw it move."""
    store = Store(args.db)
    h = store.history(args.tender)
    t = h["tender"]
    if t is None:
        print(f"No tender {args.tender} in {args.db}.")
        store.close()
        return 1
    print(f"tender {t['tender_id']}  {t['tender_number'] or ''}  "
          f"{t['family'] or ''}  state: {t['state'] or '-'}")
    for label, key in (("ministry", "ministry"), ("subject", "subject"),
                       ("published", "publish_date"),
                       ("closing", "closing_date"),
                       ("technical opening", "technical_open_date"),
                       ("financial opening", "financial_open_date"),
                       ("awarded", "awarded_date"),
                       ("awarded amount", "awarded_amount")):
        if t[key]:
            print(f"  {label:<18} {t[key]}")
    print("\n  listed in (first seen -> last seen)")
    for s in h["sightings"]:
        gone = f"   left {s['gone_at'][:16]}" if s["gone_at"] else "   listed now"
        print(f"    {s['kind']:<16} {s['first_seen'][:16]} -> "
              f"{s['last_seen'][:16]}{gone}")
    if h["fetches"]:
        print("\n  detail pages read")
        for d in h["fetches"]:
            print(f"    {d['page_type']:<16} at state {d['state'] or '-':<10} "
                  f"{d['fetched_at'][:16]}")
    if h["companies"]:
        print("\n  companies")
        for c in h["companies"]:
            value = f"{c['value']:,.2f}" if c["value"] is not None else "-"
            print(f"    {c['role']:<8} {c['stage'] or '-':<20} "
                  f"{(c['name'] or '')[:36]:<36} {c['cr_number'] or '-':<12} "
                  f"{value:>16}")
    if h["failures"]:
        print("\n  waiting to retry")
        for r in h["failures"]:
            print(f"    {r['kind']:<24} attempts {r['attempts']}  {r['error']}")
    store.close()
    return 0


def cmd_parse(args) -> int:
    """Re-parse everything on disk. No network."""
    store = Store(args.db)
    counts = {"listing": 0, "tender_details": 0, "detail": 0, "classified": 0}

    for row in list(store.pages("listing")):
        html = store.html_of(row)
        if not html or looks_rejected(html):
            continue
        kind = kind_from_url(row["url"])
        for r in parse_listing(html, kind):
            # fields only: the state comes from the sightings, not from
            # whichever old copy of a listing page happens to be on disk
            store.upsert_tender({k: v for k, v in r.items() if k != "state"},
                                source_url=row["url"])
        counts["listing"] += 1

    for row in list(store.pages("tender_details")):
        html = store.html_of(row)
        if not html or looks_rejected(html):
            continue
        parsed = parse_tender_details(html, row["tender_id"])
        if not parsed.get("found"):
            continue
        store.upsert_tender(parsed["tender"], source_url=row["url"])
        store.replace_tender_activities(row["tender_id"], parsed["activities"])
        counts["tender_details"] += 1

    for row in list(store.pages("detail")):
        html = store.html_of(row)
        if not html or looks_rejected(html):
            continue
        parsed = parse_detail(html, row["tender_id"])
        if not parsed.get("found"):
            continue
        store.upsert_tender(parsed["tender"], source_url=row["url"])
        store.replace_companies(row["tender_id"], parsed["companies"])
        counts["detail"] += 1

    for row in list(store.pages("classified")):
        html = store.html_of(row)
        if not html or looks_rejected(html):
            continue
        for r in parse_company_list(html):
            store.upsert_company(r, page=row["page"])
        counts["classified"] += 1

    for (tid,) in store.conn.execute("SELECT tender_id FROM tender").fetchall():
        store.refresh_state(tid)
    store.commit()
    print("re-parsed: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    for k, v in store.stats().items():
        print(f"{k:<26} {v}")
    store.close()
    return 0


def cmd_match(args) -> int:
    store = Store(args.db)
    try:
        customers, report = load_customers_report(args.customers, args.sheet)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_load_report(report)
    companies = store.companies()
    if not companies:
        held = store.stats()["tenders"]
        if held:
            deepest = max([r["deepest_page"] for r in store.conn.execute(
                "SELECT deepest_page FROM section_state")] or [1]) or 1
            print(f"\n{held} tenders are stored, but no companies page has "
                  "been read yet, and those are the pages that carry the "
                  "winners, the bidders and the prices -- there is nothing "
                  "to match a customer to until some are in.\n"
                  "A first load reads every listing page before it fetches "
                  "any of them. To spend a run on the detail pages instead, "
                  "keep it inside the listing pages already held:\n"
                  f"    python -m monaqasat crawl --kind awarded "
                  f"--pages 1-{deepest} --max-hours 1\n"
                  "`stats` then shows award rows; run this command again "
                  "after that.", file=sys.stderr)
        else:
            print("Nothing crawled yet. Run `crawl` first.", file=sys.stderr)
        return 1

    wanted = set(getattr(args, "client", None) or ())
    if wanted:
        customers = [c for c in customers if c["customer_id"] in wanted]
        if not customers:
            print(f"none of {', '.join(sorted(wanted))} are in "
                  f"{args.customers}", file=sys.stderr)
            return 1

    counterparties: list[dict] = []
    if getattr(args, "counterparties", None):
        try:
            counterparties, cp_report = load_counterparties(
                args.counterparties, getattr(args, "counterparty_sheet", None))
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if wanted:
            counterparties = [c for c in counterparties
                              if c["client_id"] in wanted]
        print_counterparty_report(cp_report)

    party_rows = parties(customers, counterparties)
    matches = match_parties(party_rows, companies,
                            fuzzy_threshold=args.fuzzy,
                            translit_threshold=args.translit)
    # This file's clients are matched afresh; nobody else's are touched.
    store.clear_matches(None if args.replace
                        else [c["customer_id"] for c in customers]
                        or [c["client_id"] for c in counterparties])
    store.save_matches(matches)

    s = summarise(matches)
    print(f"{len(customers)} customers"
          + (f" and {len(counterparties)} counterparties" if counterparties
             else "")
          + f" against {len(companies)} company rows")
    print(f"  matches            {s['matches']}")
    print(f"  customers matched  {s['customers matched']}")
    for method, n in s["by method"].items():
        if n:
            print(f"    {method:<16} {n}")
    print(f"  awarded rows       {s['awarded']}")
    print(f"  bid-only rows      {s['bid only']}")
    if s["needs review"]:
        print(f"\n  {s['needs review']} match(es) rest on similar names "
              "(name_fuzzy / name_translit), not a CR or an exact name. "
              "Check them before use -- they are in the --out file with "
              "their scores.")

    # Being on the classified register is itself worth reporting, even for a
    # borrower with no bidding history at all.
    if store.conn.execute("SELECT COUNT(*) FROM classified_company"
                          ).fetchone()[0]:
        register = store.conn.execute(
            "SELECT * FROM classified_company").fetchall()
        hits = match_classified(customers, register,
                                fuzzy_threshold=args.fuzzy,
                                translit_threshold=args.translit)
        print(f"\n  on the classified register: "
              f"{len({h['customer_id'] for h in hits})} of {len(customers)}")
        for row in hits[:15]:
            print(f"    {row['customer_id']:<14} {row['method']:<13} "
                  f"{row['size'] or '-':<8} {row['evaluation'] or '-':<12} "
                  f"cert to {row['certificate_end'] or '-'}")

    by_relation: dict[str, int] = {}
    for m in matches:
        by_relation[m["relation"]] = by_relation.get(m["relation"], 0) + 1
    if len(by_relation) > 1 or "self" not in by_relation:
        print("\n  by relation")
        for rel, n in sorted(by_relation.items()):
            print(f"    {rel:<14} {n}")

    if args.out:
        ids = ([c["customer_id"] for c in customers]
               + [c["client_id"] for c in counterparties])
        rows = [dict(r) for r in store.match_rows(sorted(set(ids)) or None)]
        _write_csv(args.out, rows)
        print(f"\nwrote {args.out}: {len(rows)} rows, one per company row on "
              "a tender, with the dates, the amount that company was awarded "
              "or bid, and the body that awarded it")

    if getattr(args, "features", None):
        built = build_client_months(store, months=args.feature_months)
        rows = [dict(r) for r in store.conn.execute(
            "SELECT * FROM client_month ORDER BY client_id, as_of_month")]
        _write_csv(args.features, rows)
        print(f"wrote {args.features}: {built['rows']} client-months over "
              f"{built['clients']} client(s), point-in-time -- a row dated a "
              "month uses no event after it")

    store.close()
    return 0


def cmd_analyse(args) -> int:
    """Bid-to-win conversion, per company.

    A tender is decided once its winners are known. A company on a decided
    tender either won it or lost it; a company on an undecided one has a bid
    still open (or the tender was cancelled). The win rate is wins over
    decided tenders -- counting open bids as losses would make an active
    bidder look like a poor one.
    """
    store = Store(args.db)
    rows = store.conn.execute(
        "WITH decided AS (SELECT DISTINCT tender_id FROM company"
        "                 WHERE role='awarded'),"
        " per AS (SELECT COALESCE(c.cr_root, c.name_normalised) AS key,"
        "                MIN(c.name) AS name, c.tender_id,"
        "                MAX(c.role='awarded') AS won,"
        "                SUM(CASE WHEN c.role='awarded' THEN COALESCE(c.value, 0)"
        "                    ELSE 0 END) AS value"
        "         FROM company c GROUP BY key, c.tender_id)"
        " SELECT per.key, MIN(per.name) AS name, COUNT(*) AS appearances,"
        "        SUM(per.won) AS won,"
        "        SUM(CASE WHEN per.won=0 AND d.tender_id IS NOT NULL"
        "            THEN 1 ELSE 0 END) AS lost,"
        "        SUM(CASE WHEN d.tender_id IS NULL AND t.state != 'cancelled'"
        "            THEN 1 ELSE 0 END) AS open,"
        "        SUM(CASE WHEN d.tender_id IS NULL AND t.state = 'cancelled'"
        "            THEN 1 ELSE 0 END) AS cancelled,"
        "        SUM(per.value) AS value,"
        "        MIN(t.awarded_date) AS first_award,"
        "        MAX(t.awarded_date) AS last_award"
        " FROM per JOIN tender t USING (tender_id)"
        " LEFT JOIN decided d USING (tender_id)"
        " GROUP BY per.key HAVING appearances >= ?"
        " ORDER BY won * 1.0 / MAX(won + lost, 1), appearances DESC",
        (args.min_bids,)).fetchall()
    if not rows:
        print("Nothing to analyse yet. Crawl the opened and awarded sections.")
        return 0

    print(f"companies with at least {args.min_bids} appearances: {len(rows)}")
    print(f"\n{'company':<36} {'seen':>5} {'won':>4} {'lost':>5} {'open':>5} "
          f"{'rate':>6} {'value QAR':>16}")
    print("-" * 82)
    for r in rows[:args.limit]:
        decided = r["won"] + r["lost"]
        rate = f"{r['won'] / decided:>5.0%}" if decided else "    -"
        print(f"{(r['name'] or '')[:35]:<36} {r['appearances']:>5} "
              f"{r['won']:>4} {r['lost']:>5} {r['open']:>5} {rate:>6} "
              f"{(r['value'] or 0):>16,.0f}")

    if args.out:
        _write_csv(args.out, [dict(r) for r in rows])
        print(f"\nwrote {args.out}")
    store.close()
    return 0


def cmd_features(args) -> int:
    from datetime import date as date_cls
    from .features import build_features
    store = Store(args.db)
    as_of = date_cls.fromisoformat(args.as_of_end) if args.as_of_end else None
    result = build_features(store, months=args.months, as_of_end=as_of)
    print(f"crs {result['crs']}  rows {result['rows']}  months {result['months']}")
    store.close()
    return 0


def cmd_stats(args) -> int:
    store = Store(args.db)
    for k, v in store.stats().items():
        print(f"{k:<26} {v}")
    by_state = store.by_state()
    if by_state:
        print(f"\n{'family':<8} {'state':<12} {'count':>7} {'value QAR':>18}")
        print("-" * 48)
        for r in by_state:
            print(f"{r['family']:<8} {r['state']:<12} {r['n']:>7} "
                  f"{(r['value'] or 0):>18,.0f}")
    store.close()
    return 0


def cmd_pack(args) -> int:
    """Write a copy with no stored HTML, small enough to put in git."""
    from .store import PACK_SKIP, pack_database
    src = Path(args.db)
    if not src.exists():
        print(f"no database at {src}", file=sys.stderr)
        return 1
    before = src.stat().st_size
    size = pack_database(str(src), args.out)
    print(f"packed {before/1e6:.0f} MB -> {size/1e6:.1f} MB")
    print(f"wrote {args.out}")
    print("HTML is not in this file. On the other laptop, copy it to")
    print("monaqasat.db and keep harvesting; crawl progress is included.")
    print("Left out: " + ", ".join(PACK_SKIP)
          + " -- match holds your customers' ids and names, so a packed copy"
          " is safe to share.")
    return 0


def cmd_compact(args) -> int:
    """Shrink the database: compress stored pages, then reclaim the space.

    Pages are compressed as they are fetched from v0.5 on. This command is
    for a database written before that, and for reclaiming the file space
    afterwards. Nothing parsed is touched, and every page still reads back
    exactly as it was fetched.
    """
    path = Path(args.db)
    if not path.exists():
        print(f"no database at {path}", file=sys.stderr)
        return 1
    before = path.stat().st_size
    store = Store(args.db)
    by_kind = store.stored_bytes()
    if by_kind:
        print("stored pages, before:")
        for kind, n in by_kind.items():
            print(f"  {kind:16} {(n or 0)/1e6:8.1f} MB")

    def tick(done: int) -> None:
        print(f"  compressed {done}...", flush=True)

    result = store.compact(progress=tick if not args.quiet else None,
                           drop_before=args.drop_before)
    if result["compressed"]:
        print(f"compressed {result['compressed']} pages: "
              f"{result['bytes before']/1e6:.0f} MB of HTML -> "
              f"{result['bytes after']/1e6:.1f} MB stored "
              f"({result['bytes before'] / max(result['bytes after'], 1):.1f}x)")
    else:
        print("every page was already compressed")
    if result["dropped"]:
        print(f"dropped the stored copy of {result['dropped']} page(s) "
              f"fetched before {args.drop_before}; their parsed rows and "
              "content hashes stay")
    store.close()
    if not args.no_vacuum:
        print("reclaiming file space (VACUUM)...", flush=True)
        conn = sqlite3.connect(args.db, isolation_level=None)
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    after = path.stat().st_size
    print(f"database {before/1e6:.0f} MB -> {after/1e6:.1f} MB")
    return 0


def cmd_export(args) -> int:
    store = Store(args.db)
    sql = {
        "awards": "SELECT c.*, t.family, t.state, t.awarded_date, t.ministry,"
                  " t.awarded_amount FROM company c JOIN tender t"
                  " USING (tender_id)",
        "tenders": "SELECT * FROM tender",
        "sightings": "SELECT * FROM sighting ORDER BY tender_id, first_seen",
        "companies": "SELECT * FROM classified_company",
        "activities": "SELECT * FROM company_activity",
        "tender_activities": "SELECT * FROM tender_activity",
        "matches": "SELECT * FROM match",
        "features": "SELECT * FROM feature_cr_month",
    }[args.table]
    rows = [dict(r) for r in store.conn.execute(sql)]
    if not rows:
        print(f"Nothing in {args.table}.", file=sys.stderr)
        return 1
    _write_csv(args.out, rows)
    print(f"wrote {len(rows)} rows to {args.out}")
    store.close()
    return 0


def cmd_harvest(args) -> int:
    """Everything, until the store is complete or the clock runs out.

    The classified register first (new companies, plus a slice of its
    refresh pass), then one crawl of every tender and bid section: listings
    first, detail pages after. Each page is committed as it is fetched, so
    closing the laptop or a --max-hours cut-off never loses progress. A
    nightly scheduled task finishes a first load over several nights; after
    that the same command keeps every section current (see FIXES.md).
    """
    _set_deadline(args)
    deadline = getattr(args, "deadline", None)

    print("=== classified register ===")
    cmd_companies(argparse.Namespace(
        db=args.db, pages=None,
        full=bool(getattr(args, "full_register", False)),
        delay=args.delay, timeout=args.timeout,
        max_hours=None, deadline=deadline,
        register_pages=getattr(args, "register_pages", None),
        register_days=_opt(args, "register_days", REGISTER_DAYS),
    ))
    if _expired(args):
        print("\ntime budget reached. Re-run harvest to continue.")
        cmd_status(argparse.Namespace(db=args.db, runs=5))
        return 0

    print("\n=== tenders and bids ===")
    cmd_crawl(argparse.Namespace(
        db=args.db, kind="all", pages=None, full=False, quick=False,
        stop_after=2, limit_details=None, delay=args.delay,
        timeout=args.timeout, listings_only=False,
        no_details=args.no_details, max_hours=None, deadline=deadline,
        lookback_days=_opt(args, "lookback_days", LOOKBACK_DAYS),
        roll_pages=getattr(args, "roll_pages", None),
        sweep_days=_opt(args, "sweep_days", SWEEP_DAYS),
    ))

    cmd_status(argparse.Namespace(db=args.db, runs=8))
    store = Store(args.db)
    done = {r["kind"]: r["backfill_complete"]
            for r in store.conn.execute(
                "SELECT kind, backfill_complete FROM section_state")}
    store.close()
    wanted = ["classified", *LISTING_KINDS]
    missing = [k for k in wanted if not done.get(k)]
    if missing:
        print(f"\nstill to finish: {', '.join(missing)}")
        print("Re-run harvest (or the scheduled task) until this list is empty.")
        return 0
    print("\nFirst load complete. Each run from now on reads the small "
          "sections in full, catches up Awarded and Cancelled, continues the "
          "rolling passes, and fetches detail pages only where something "
          "changed.")
    return 0


def cmd_profile(args) -> int:
    store = Store(args.db)
    rows = store.conn.execute(
        "SELECT m.tender_id, m.role, m.method, m.score, m.matched_name,"
        " m.matched_cr, t.tender_number, t.ministry, t.family, t.state,"
        " t.awarded_date, t.closing_date, c.value, t.awarded_amount,"
        " t.local_value_system,"
        " EXISTS (SELECT 1 FROM company w WHERE w.tender_id = m.tender_id"
        "         AND w.role = 'awarded') AS decided"
        " FROM match m JOIN tender t USING (tender_id)"
        " JOIN company c ON c.tender_id=m.tender_id AND c.role=m.role"
        "                AND c.seq=m.seq"
        " WHERE m.client_id=? AND m.relation='self'"
        " ORDER BY COALESCE(t.awarded_date, t.closing_date) DESC",
        (args.customer,)).fetchall()

    crs = {r["matched_cr"] for r in rows if r["matched_cr"]}
    reg = None
    for cr in crs:
        reg = store.conn.execute(
            "SELECT * FROM classified_company WHERE cr_root=?",
            (cr_root(cr),)).fetchone()
        if reg is not None:
            break

    if not rows and reg is None:
        print(f"Nothing recorded for {args.customer}.")
        return 0

    print(args.customer)
    if reg is not None:
        listed = ("" if not reg["delisted_at"]
                  else f"  (no longer listed since {reg['delisted_at'][:10]})")
        print(f"  classified      : {reg['size'] or '-'}, evaluation "
              f"{reg['evaluation'] or '-'}, certificate to "
              f"{reg['certificate_end'] or '-'}{listed}")
        for a in store.conn.execute(
                "SELECT activity_code, activity_name, grade"
                " FROM company_activity WHERE profile_number=? LIMIT 6",
                (reg["profile_number"],)).fetchall():
            print(f"    {a['activity_code']:<10} {a['grade'] or '-':<10} "
                  f"{(a['activity_name'] or '')[:40]}")

    tenders = {r["tender_id"] for r in rows}
    won = {r["tender_id"] for r in rows if r["role"] == "awarded"}
    decided = {r["tender_id"] for r in rows if r["decided"]}
    lost = decided - won
    open_ = {r["tender_id"] for r in rows
             if not r["decided"] and r["state"] != "cancelled"}
    value = sum(r["value"] or 0 for r in rows if r["role"] == "awarded")
    print(f"  tenders seen in : {len(tenders)}")
    print(f"  won / lost / open: {len(won)} / {len(lost)} / {len(open_)}")
    print(f"  awarded value   : {value:,.2f} QAR")
    if won or lost:
        print(f"  win rate        : {len(won) / len(won | lost):.0%} of "
              f"{len(won | lost)} decided")
    entities = {r["ministry"] for r in rows
                if r["role"] == "awarded" and r["ministry"]}
    if entities and value:
        by_entity = {e: sum(r["value"] or 0 for r in rows
                            if r["role"] == "awarded" and r["ministry"] == e)
                     for e in entities}
        top = max(by_entity, key=by_entity.get)
        print(f"  awarding bodies : {len(entities)}"
              f"  (largest {top}, {by_entity[top] / value:.0%} of value)")
    review = [r for r in rows if r["method"] in ("name_fuzzy", "name_translit")]
    if review:
        print(f"  note            : {len(review)} row(s) matched on a similar "
              "name only (marked *) -- check before relying on them")

    print(f"\n  {'date':<11} {'state':<10} {'role':<8} {'value':>14}  body")
    for r in rows[:30]:
        when = r["awarded_date"] or r["closing_date"] or "-"
        flag = "*" if r["method"] in ("name_fuzzy", "name_translit") else " "
        print(f" {flag}{when:<11} {r['state'] or '-':<10} {r['role']:<8} "
              f"{(r['value'] or 0):>14,.0f}  {(r['ministry'] or '')[:32]}")
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


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="monaqasat",
        description="Harvest Qatar state procurement -- tenders, bids and the "
                    "classified company register -- and match it to your book.")
    sub = p.add_subparsers(dest="command", required=True)

    def db(sp):
        sp.add_argument("--db", default=DEFAULT_DB)

    def crawl_options(sp):
        sp.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS,
                        help="Awarded head scan: keep reading until award "
                             "dates are this many days older than the newest "
                             f"already held (default {LOOKBACK_DAYS})")
        sp.add_argument("--roll-pages", type=int,
                        help="cap the rolling full pass over Awarded and "
                             "Cancelled at this many pages per run. By "
                             "default the slice is sized from the gap since "
                             "the last run, so the pass finishes within "
                             f"--sweep-days at any frequency (never below "
                             f"{ROLL_PAGES} pages)")
        sp.add_argument("--sweep-days", type=float, default=SWEEP_DAYS,
                        help="start a new rolling pass this many days after "
                             f"the last one finished (default {SWEEP_DAYS})")

    kind_help = ("section: a name from " + ", ".join(LISTING_KINDS)
                 + " -- or all | tenders | bids | companies")

    sp = sub.add_parser("doctor",
                        help="diagnose connection failures, no writes")
    sp.add_argument("--host", default="monaqasat.mof.gov.qa")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("probe", help="size every section, no writes")
    sp.add_argument("--kind", default="all", help=kind_help)
    sp.add_argument("--delay", type=float, default=1.5)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser(
        "crawl", help="bring sections up to date (incremental)",
        description="Reads every listing first, then detail pages. Small "
                    "sections are read end to end each run, which is how a "
                    "tender that moved (Available -> Closed -> Opened) is "
                    "noticed wherever it lands. Awarded and Cancelled are "
                    "caught up from the top and re-read by a rolling pass. "
                    "Safe to run as often as you like.")
    db(sp)
    sp.add_argument("--kind", default="awarded", help=kind_help)
    sp.add_argument("--pages", help="restrict to a range, e.g. 1-100")
    sp.add_argument("--full", action="store_true",
                    help="read every page of every selected section now")
    sp.add_argument("--quick", action="store_true",
                    help="only read from the top until caught up, in every "
                         "section. Fast, but misses tenders that move into "
                         "the middle of a list until the next normal run.")
    sp.add_argument("--stop-after", type=int, default=2,
                    help="consecutive already-known pages that mean 'caught "
                         "up' (default 2)")
    sp.add_argument("--limit-details", type=int,
                    help="fetch at most this many detail pages this run")
    sp.add_argument("--delay", type=float, default=1.5)
    sp.add_argument("--timeout", type=float, default=120.0,
                    help="seconds to wait for a slow page (default 120)")
    sp.add_argument("--listings-only", action="store_true")
    sp.add_argument("--no-details", action="store_true",
                    help="skip TenderDetails pages: faster, less detail")
    sp.add_argument("--max-hours", type=float,
                    help="stop cleanly after this many hours; re-run to continue")
    crawl_options(sp)
    sp.set_defaults(func=cmd_crawl)

    def register_options(sp):
        sp.add_argument("--register-pages", type=int,
                        help="cap the register refresh at this many pages "
                             "per run. By default the slice is sized from "
                             "the gap since the last run, so a pass finishes "
                             f"within --register-days (never below "
                             f"{REGISTER_PAGES} pages)")
        sp.add_argument("--register-days", type=float, default=REGISTER_DAYS,
                        help="start a new register refresh pass this many "
                             f"days after the last (default {REGISTER_DAYS})")

    sp = sub.add_parser(
        "companies", help="crawl the classified register (incremental)",
        description="Reads the pages new companies land on, retries failed "
                    "pages, and refreshes a slice of the register each run so "
                    "evaluations, certificate dates and removals are picked "
                    "up. --full refreshes everything in one go.")
    db(sp)
    sp.add_argument("--pages")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--delay", type=float, default=1.5)
    sp.add_argument("--timeout", type=float, default=120.0)
    sp.add_argument("--max-hours", type=float)
    register_options(sp)
    sp.set_defaults(func=cmd_companies)

    sp = sub.add_parser(
        "harvest",
        help="crawl every section until complete, or until --max-hours",
        description="Classified register first, then every tender/bid "
                    "section. Progress is committed per page. Run under "
                    "Task Scheduler with --max-hours so a first load "
                    "finishes over several nights without the laptop "
                    "staying open.")
    db(sp)
    sp.add_argument("--delay", type=float, default=1.5)
    sp.add_argument("--timeout", type=float, default=120.0)
    sp.add_argument("--max-hours", type=float, default=6.0,
                    help="stop cleanly after this many hours (default 6)")
    sp.add_argument("--no-details", action="store_true")
    sp.add_argument("--full-register", action="store_true",
                    help="refresh every classified company, not just new ones")
    crawl_options(sp)
    register_options(sp)
    sp.set_defaults(func=cmd_harvest)

    sp = sub.add_parser("status", help="progress, pending retries, recent runs")
    db(sp)
    sp.add_argument("--runs", type=int, default=8)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("history",
                        help="one tender's lifecycle as the crawl saw it")
    db(sp)
    sp.add_argument("--tender", required=True, help="tender id, e.g. 659460")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("parse", help="re-parse stored HTML, no network")
    db(sp)
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("match", help="join your customers to the data")
    db(sp)
    sp.add_argument("--customers", required=True,
                    help=".xlsx or .csv -- customer_id, name, cr_number "
                         "(and name_ar); common variants of those headers "
                         "are recognised")
    sp.add_argument("--sheet", help="worksheet name, for .xlsx (default first)")
    sp.add_argument("--counterparties",
                    help="a client's top buyers and suppliers from the "
                         "transaction model -- client_id, name, direction "
                         "(and optionally rank, amount). These carry no CR "
                         "number, so they are matched on the name alone, at "
                         "a stricter bar, and every one is flagged")
    sp.add_argument("--counterparty-sheet",
                    help="worksheet name for an .xlsx counterparty list")
    sp.add_argument("--client", action="append",
                    help="only these client ids (repeatable) -- for a live "
                         "run on one applicant")
    sp.add_argument("--features",
                    help="also write the client-by-month feature table here")
    sp.add_argument("--feature-months", type=int, default=60,
                    help="how many months of features to build (default 60)")
    sp.add_argument("--fuzzy", type=float, default=FUZZY_THRESHOLD,
                    help="similar-name threshold, same script "
                         f"(default {FUZZY_THRESHOLD}; 1 turns it off)")
    sp.add_argument("--translit", type=float, default=TRANSLIT_THRESHOLD,
                    help="Arabic <-> Latin skeleton threshold "
                         f"(default {TRANSLIT_THRESHOLD}; 1 turns it off)")
    sp.add_argument("--replace", action="store_true",
                    help="clear every stored match first, not just this "
                         "file's customers")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_match)

    sp = sub.add_parser("analyse", help="bid-to-win conversion by company")
    db(sp)
    sp.add_argument("--min-bids", type=int, default=2)
    sp.add_argument("--limit", type=int, default=40)
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_analyse)

    sp = sub.add_parser("features",
                        help="rebuild CR-by-month feature table")
    db(sp)
    sp.add_argument("--months", type=int, default=60)
    sp.add_argument("--as-of-end", help="YYYY-MM-DD, defaults to today")
    sp.set_defaults(func=cmd_features)

    sp = sub.add_parser("stats", help="what is in the store")
    db(sp)
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser(
        "pack",
        help="copy the database without HTML so it can go in git",
    )
    db(sp)
    sp.add_argument("--out", default="data/monaqasat.slim.db")
    sp.set_defaults(func=cmd_pack)

    sp = sub.add_parser(
        "compact",
        help="compress stored pages and reclaim file space",
        description="Pages fetched from v0.5 on are compressed as they "
                    "arrive. Run this once on a database written before "
                    "that: it compresses what is already stored (about ten "
                    "times smaller) and reclaims the space. Every page still "
                    "reads back exactly as fetched.")
    db(sp)
    sp.add_argument("--no-vacuum", action="store_true",
                    help="skip reclaiming the file space (VACUUM needs room "
                         "for a second copy while it runs)")
    sp.add_argument("--quiet", action="store_true")
    sp.add_argument("--drop-before",
                    help="also drop the stored copy of pages fetched before "
                         "this date (YYYY-MM-DD). Their parsed rows and "
                         "content hashes stay, but they can no longer be "
                         "re-parsed without re-crawling.")
    sp.set_defaults(func=cmd_compact)

    sp = sub.add_parser("export", help="dump a table to CSV")
    db(sp)
    sp.add_argument("--table", default="awards",
                    choices=["awards", "tenders", "sightings", "companies",
                             "activities", "tender_activities", "matches",
                             "features"])
    sp.add_argument("--out", default="export.csv")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("profile", help="one customer's procurement record")
    db(sp)
    sp.add_argument("--customer", required=True)
    sp.set_defaults(func=cmd_profile)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
