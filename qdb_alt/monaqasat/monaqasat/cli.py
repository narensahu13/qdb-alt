"""Command line.

    python -m monaqasat doctor                       why won't it connect?
    python -m monaqasat probe                        size every section
    python -m monaqasat harvest --max-hours 6         unattended, resumable
    python -m monaqasat crawl --kind all --full      every listing page
    python -m monaqasat companies                    the classified register
    python -m monaqasat status                       progress and retries
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
import sys
import time
from pathlib import Path

from .fetch import Blocked, Fetcher, PageTimeout
from .match import (load_customers_report, match_classified, match_customers,
                    print_load_report, summarise)
from .parse import (BID_KINDS, COMPANY_KINDS, LISTING_KINDS, TENDER_KINDS,
                    kind_from_url, kind_path, last_listing_page, looks_rejected,
                    parse_company_list, parse_detail, parse_listing,
                    parse_tender_details)
from .store import Store

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
# Page numbers are not stable. Every listing is newest-first, so each tender
# published this week pushes every older tender further down: page 90 today
# is roughly page 92 next week. Tracking progress by page number would, on a
# periodic run, either miss the new tenders (page 1 "already done") or
# re-download everything. So progress is tracked by tender id and section,
# and listing pages are only ever read to discover what is new.
#
# Listing pages are cheap -- one request covers twenty tenders. Detail pages
# are the cost -- up to two requests per tender -- and those are fetched only
# for tenders that are new, or whose state has advanced since last time.
# --------------------------------------------------------------------------

MARGIN = 3              # overlap when resuming, since pages drift
MAX_CONSECUTIVE_FAILS = 5


def _expired(args) -> bool:
    deadline = getattr(args, "deadline", None)
    return deadline is not None and time.monotonic() >= deadline


def _set_deadline(args) -> None:
    hours = getattr(args, "max_hours", None)
    if hours and not getattr(args, "deadline", None):
        args.deadline = time.monotonic() + hours * 3600


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
    """Record what a listing page showed. Returns how many were new here."""
    spec = LISTING_KINDS[kind]
    new = moved = 0
    for r in rows:
        is_new, advanced = store.record_sighting(
            r["tender_id"], kind, spec["family"], spec["state"])
        store.upsert_tender(r, source_url=url)
        new += is_new
        moved += advanced
    store.bump(run_id, new_tenders=new, state_changes=moved)
    store.commit()
    return new


def _walk_listings(store, f, kind, args, run_id) -> str:
    """Read listing pages for one section. Returns a one-line outcome.

    Update mode, in three passes:
      1. newest pages, until two in a row hold nothing new -- caught up
      2. if the first load never finished, carry on from where it stopped
      3. re-read the neighbourhood of any page that failed before, shifted
         by however far new tenders have pushed it down since
    A section is only marked complete when it has been read end to end
    with no page left failed. A load with a hole in it is not complete.
    """
    sec = store.section(kind)
    rel1 = f.listing_path(kind_path(kind), 1)
    failed_before = sorted({int(r["ref"]) for r in store.failures(kind)
                            if str(r["ref"]).isdigit()})

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

    def visit(page) -> int | None:
        nonlocal deepest, fails
        if _expired(args):
            return None
        visited.add(page)
        html, rows = _fetch_listing(store, f, kind, page, run_id)
        if html is None:
            fails += 1
            return None
        fails = 0
        deepest = max(deepest, page)
        store.update_section(kind, deepest_page=deepest)
        url = f"{f.base}{f.listing_path(kind_path(kind), page)}"
        return _absorb(store, kind, rows, url, run_id)

    def settle() -> bool:
        """Record whether the section is read to the end, and whether it
        is complete -- read to the end with no page left failed."""
        reached = bool(store.section(kind)["reached_end"]) or \
            (hi >= last and deepest >= last)
        if reached:
            # Content is contiguous to the last page; as new tenders push
            # everything down, "the end" moves with it.
            store.update_section(kind, reached_end=1, deepest_page=last)
        clean = not store.failures(kind)
        store.update_section(kind, backfill_complete=int(reached and clean))
        return reached and clean

    pages = [p for p in in_range if p != 1]

    if args.full:
        for page in pages:
            if _expired(args):
                settle()
                return f"time budget reached at page {deepest} of {last}"
            visit(page)
            if fails >= MAX_CONSECUTIVE_FAILS:
                return f"stopped after {fails} failed pages in a row"
        done = settle()
        return (f"full walk of {len(in_range)} pages"
                + ("" if done else "; some pages failed, retried next run"))

    # 1. newest pages until caught up ----------------------------------
    streak = 0 if fresh_total else 1
    caught_up = 1
    for page in pages:
        if _expired(args):
            settle()
            return f"time budget reached at page {deepest} of {last}"
        if streak >= args.stop_after:
            break
        n = visit(page)
        caught_up = page
        if fails >= MAX_CONSECUTIVE_FAILS:
            return f"stopped after {fails} failed pages in a row"
        if n is None:
            continue
        fresh_total += n
        streak = streak + 1 if n == 0 else 0
    outcome = f"caught up at page {caught_up}"

    shift = fresh_total // per_page

    # 2. finish an interrupted first load --------------------------------
    # Only if it genuinely never reached the end. A load that reached the
    # end with a hole in it is handled by pass 3, not by re-reading the tail.
    if not sec["reached_end"]:
        resume_from = max(caught_up + 1, sec["deepest_page"] + shift - MARGIN)
        rest = [p for p in pages if p >= resume_from and p not in visited]
        if rest:
            print(f"  {kind}: first load incomplete -- continuing from page "
                  f"{rest[0]} to {rest[-1]}", flush=True)
        for page in rest:
            if _expired(args):
                settle()
                return f"time budget reached at page {deepest} of {last}"
            visit(page)
            if fails >= MAX_CONSECUTIVE_FAILS:
                return f"stopped after {fails} failed pages in a row"
            if page % 25 == 0:
                print(f"    {kind} page {page}/{rest[-1]}", flush=True)

    # 3. revisit gaps left by earlier failures ---------------------------
    # Every tender published since then has pushed the lost page down.
    healed = 0
    for old in failed_before:
        window = [p for p in range(old + shift - MARGIN, old + shift + MARGIN + 1)
                  if lo <= p <= hi and p <= last]
        todo = [p for p in window if p not in visited]
        ok = True
        for page in todo:
            if _expired(args):
                settle()
                return f"time budget reached at page {deepest} of {last}"
            if visit(page) is None:
                ok = False
            if fails >= MAX_CONSECUTIVE_FAILS:
                return f"stopped after {fails} failed pages in a row"
        # the window around the old position has now been read cleanly, so
        # whatever sat on the lost page has been picked up
        if ok and window:
            store.clear_failure(kind, str(old))
            healed += 1
    if healed:
        outcome += f"; recovered {healed} previously failed page(s)"

    if settle():
        if not sec["backfill_complete"]:
            outcome += "; first load complete"
    elif not store.section(kind)["reached_end"]:
        outcome += f"; first load reached page {deepest} of {last}"
    elif store.failures(kind):
        outcome += f"; {len(store.failures(kind))} page(s) still to retry"
    return outcome


def _fetch_details(store, f, kind, args, run_id) -> str:
    """Detail pages for every tender in the section that needs them."""
    spec = LISTING_KINDS[kind]
    jobs: list[tuple[str, str, str | None]] = []
    if not args.no_details:
        jobs += [("tender_details", tid, st)
                 for tid, st in store.pending_details(kind, "tender_details")]
    if spec["has_companies"]:
        jobs += [("companies", tid, st)
                 for tid, st in store.pending_details(kind, "companies")]
    if not jobs:
        return "details up to date"
    if args.limit_details:
        jobs = jobs[:args.limit_details]

    print(f"  {kind}: {len(jobs)} detail pages to fetch", flush=True)
    done = fails = 0
    for n, (ptype, tid, state) in enumerate(jobs, 1):
        if _expired(args):
            return f"{done} detail pages, then time budget reached"
        rel = (f"/TendersOnlineServices/TenderDetails/{tid}"
               if ptype == "tender_details" else f.detail_path(tid))
        url = f"{f.base}{rel}"
        try:
            html, status = f.get(rel)
        except Blocked as exc:
            store.record_failure(f"{kind}:{ptype}", tid, url, str(exc))
            store.bump(run_id, failures=1)
            fails += 1
            if fails >= MAX_CONSECUTIVE_FAILS:
                return (f"{done} detail pages, then stopped after {fails} "
                        "failures in a row")
            continue
        if ptype == "tender_details":
            parsed = parse_tender_details(html, tid)
            if not parsed.get("found"):
                store.record_failure(f"{kind}:{ptype}", tid, url,
                                     "not a tender details page")
                store.bump(run_id, failures=1)
                fails += 1
                if fails >= MAX_CONSECUTIVE_FAILS:
                    return (f"{done} detail pages, then stopped after {fails} "
                            "failures in a row")
                continue
            store.save_page(url, "tender_details", html, status=status,
                            tender_id=tid)
            store.upsert_tender(parsed["tender"], source_url=url)
            store.replace_tender_activities(tid, parsed["activities"])
        else:
            parsed = parse_detail(html, tid)
            if not parsed.get("found"):
                store.record_failure(f"{kind}:{ptype}", tid, url,
                                     "not a tender page (home/empty)")
                store.bump(run_id, failures=1)
                fails += 1
                if fails >= MAX_CONSECUTIVE_FAILS:
                    return (f"{done} detail pages, then stopped after {fails} "
                            "failures in a row")
                continue
            store.save_page(url, "detail", html, status=status, tender_id=tid)
            store.upsert_tender(parsed["tender"], source_url=url)
            store.replace_companies(tid, parsed["companies"])
        fails = 0
        store.mark_detail(tid, ptype, state)
        store.clear_failure(f"{kind}:{ptype}", tid)
        store.bump(run_id, details=1)
        store.commit()
        done += 1
        if n % 50 == 0:
            print(f"    {n}/{len(jobs)}", flush=True)
    return f"{done} detail pages fetched"


def _print_run(row) -> None:
    secs = ""
    try:
        from datetime import datetime
        a = datetime.fromisoformat(row["started_at"])
        b = datetime.fromisoformat(row["finished_at"])
        mins = (b - a).total_seconds() / 60
        secs = f" in {mins:.1f} min"
    except Exception:                               # noqa: BLE001, S110
        pass
    print(f"\nrun {row['run_id']} ({row['mode']}){secs}")
    print(f"  listing pages read   {row['listing_pages']}")
    print(f"  new tenders          {row['new_tenders']}")
    print(f"  state changes        {row['state_changes']}")
    print(f"  detail pages fetched {row['details']}")
    if row["companies"]:
        print(f"  companies updated    {row['companies']}")
    if row["failures"]:
        print(f"  failed, will retry   {row['failures']}")


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

    mode = "full" if args.full else "update"
    kinds = _kinds(args.kind)
    run_id = store.start_run("crawl", args.kind, mode)

    for i, kind in enumerate(kinds):
        if i and _expired(args):
            print("  time budget reached -- re-run to continue")
            break
        print(f"\n{kind}")
        outcome = _walk_listings(store, f, kind, args, run_id)
        print(f"  listings: {outcome}")
        if args.listings_only or _expired(args):
            continue
        print(f"  details:  {_fetch_details(store, f, kind, args, run_id)}")

    _print_run(store.finish_run(run_id))
    pending = store.failures()
    if pending:
        print(f"\n{len(pending)} pages failed and will be retried next run "
              "(see `status`).")
    store.close()
    return 0


def cmd_companies(args) -> int:
    """The classified register.

    Unlike the tender listings, this one is oldest-first: page 1 holds the
    earliest profile numbers and new companies land on the last page. So an
    update only needs the tail. Existing companies do change -- evaluations,
    certificate dates, activities -- which is what --full is for.
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
    if args.pages:
        pages = [p for p in _pages(args.pages) if 1 <= p <= last]
        mode = "pages"
    elif args.full:
        pages = list(range(1, last + 1))
        mode = "full"
    elif not sec["reached_end"]:
        # The register grows at the end, so the front does not drift and a
        # resume needs no safety margin -- only the last page read, in case a
        # deleted company pulled a neighbour back across the boundary.
        start = max(1, sec["deepest_page"])
        pages = sorted(set(range(start, last + 1)) | set(retry))
        mode = "first load" if start == 1 else f"resume from page {start}"
    else:
        prev_last = sec["last_page_seen"] or last
        start = max(1, prev_last - 1)
        pages = sorted(set(range(start, last + 1)) | set(retry))
        mode = "update"

    print(f"register: {last} pages -- {mode}, {len(pages)} to fetch"
          + (f" (including {len(retry)} retries)" if retry and mode != "full"
             else ""))
    run_id = store.start_run("companies", "classified", mode)
    store.update_section(kind, last_page_seen=last)

    deepest = sec["deepest_page"]
    fails = total = 0
    timed_out = False
    for page in pages:
        if _expired(args):
            timed_out = True
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
                print(f"\nstopping: {fails} pages in a row failed. The site "
                      "may be slow or refusing; try later.", file=sys.stderr)
                break
            continue
        fails = 0
        store.save_page(url, "classified", html, status=status, page=page)
        rows = parse_company_list(html)
        for r in rows:
            store.upsert_company(r, page=page)
        store.mark_page(kind, page, len(rows))
        store.clear_failure(kind, str(page))
        store.bump(run_id, listing_pages=1, companies=len(rows))
        store.commit()
        total += len(rows)
        deepest = max(deepest, page)
        store.update_section(kind, deepest_page=deepest)
        if page % 10 == 0 or page == pages[-1]:
            print(f"  page {page}/{last}  {total} companies", flush=True)

    if deepest >= last:
        store.update_section(kind, reached_end=1)
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
        "SELECT s.*, (SELECT COUNT(*) FROM sighting g WHERE g.kind=s.kind) n"
        " FROM section_state s ORDER BY s.kind").fetchall()
    if rows:
        print(f"{'section':<18} {'tenders':>8} {'pages':>6} {'reached':>8}  "
              f"first load   last update")
        print("-" * 78)
        for r in rows:
            n = r["n"] if r["kind"] != "classified" else store.conn.execute(
                "SELECT COUNT(*) FROM classified_company").fetchone()[0]
            print(f"{r['kind']:<18} {n:>8} {r['last_page_seen'] or '-':>6} "
                  f"{r['deepest_page']:>8}  "
                  f"{'complete' if r['backfill_complete'] else 'in progress':<12}"
                  f" {(r['last_update'] or '-')[:16]}")
    else:
        print("Nothing crawled yet.")

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
                  f"{r['command']:<9} {r['target']:<12} {r['mode']:<10} "
                  f"new {r['new_tenders']:>5}  details {r['details']:>5}  "
                  f"failed {r['failures']:>3}")
    store.close()
    return 0


def cmd_parse(args) -> int:
    """Re-parse everything on disk. No network."""
    store = Store(args.db)
    counts = {"listing": 0, "tender_details": 0, "detail": 0, "classified": 0}

    for row in list(store.pages("listing")):
        if not row["html"] or looks_rejected(row["html"]):
            continue
        kind = kind_from_url(row["url"])
        if kind is None:
            guessed = store.conn.execute(
                "SELECT kind FROM crawl_state WHERE page=? LIMIT 1",
                (row["page"],)).fetchone()
            kind = guessed[0] if guessed else None
        for r in parse_listing(row["html"], kind):
            store.upsert_tender(r, source_url=row["url"])
        counts["listing"] += 1

    for row in list(store.pages("tender_details")):
        if not row["html"] or looks_rejected(row["html"]):
            continue
        parsed = parse_tender_details(row["html"], row["tender_id"])
        if not parsed.get("found"):
            continue
        store.upsert_tender(parsed["tender"], source_url=row["url"])
        store.replace_tender_activities(row["tender_id"], parsed["activities"])
        counts["tender_details"] += 1

    for row in list(store.pages("detail")):
        if not row["html"] or looks_rejected(row["html"]):
            continue
        parsed = parse_detail(row["html"], row["tender_id"])
        if not parsed.get("found"):
            continue
        store.upsert_tender(parsed["tender"], source_url=row["url"])
        store.replace_companies(row["tender_id"], parsed["companies"])
        counts["detail"] += 1

    for row in list(store.pages("classified")):
        if not row["html"] or looks_rejected(row["html"]):
            continue
        for r in parse_company_list(row["html"]):
            store.upsert_company(r, page=row["page"])
        counts["classified"] += 1

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
        print("No company rows yet. Run `crawl` first.", file=sys.stderr)
        return 1

    matches = match_customers(customers, companies, fuzzy_threshold=args.fuzzy)
    if args.replace:
        store.clear_matches()
    store.save_matches(matches)

    print(f"{len(customers)} customers against {len(companies)} company rows")
    for k, v in summarise(matches).items():
        print(f"  {k:<18} {v}")

    # Being on the classified register is itself worth reporting, even for a
    # borrower with no bidding history at all.
    if store.conn.execute("SELECT COUNT(*) FROM classified_company"
                          ).fetchone()[0]:
        register = store.conn.execute(
            "SELECT * FROM classified_company").fetchall()
        hits = match_classified(customers, register,
                                fuzzy_threshold=args.fuzzy)
        print(f"\n  on the classified register: {len(hits)} of "
              f"{len(customers)}")
        for row in hits[:15]:
            print(f"    {row['customer_id']:<14} {row['size'] or '-':<8} "
                  f"{row['evaluation'] or '-':<12} "
                  f"cert to {row['certificate_end'] or '-'}")

    if args.out:
        _write_csv(args.out, matches)
        print(f"\nwrote {args.out}")
    store.close()
    return 0


def cmd_analyse(args) -> int:
    """Bid-to-win conversion, per company.

    A company in a technical or financial opening table has bid. A company in
    an awarded table has won. The gap is the signal: a borrower bidding
    steadily and never winning is a different risk from one that never bids.
    """
    store = Store(args.db)
    rows = store.conn.execute(
        "SELECT COALESCE(c.cr_root, c.name_normalised) AS key,"
        "       MIN(c.name) AS name, c.cr_root,"
        "       COUNT(DISTINCT CASE WHEN c.role='awarded'"
        "             THEN c.tender_id END) AS won,"
        "       COUNT(DISTINCT c.tender_id) AS appearances,"
        "       SUM(CASE WHEN c.role='awarded' THEN c.value ELSE 0 END) AS value,"
        "       MIN(t.awarded_date) AS first_seen,"
        "       MAX(t.awarded_date) AS last_seen"
        " FROM company c JOIN tender t USING (tender_id)"
        " GROUP BY key HAVING appearances >= ?"
        " ORDER BY won * 1.0 / appearances, appearances DESC",
        (args.min_bids,)).fetchall()
    if not rows:
        print("Nothing to analyse yet. Crawl the opened and awarded sections.")
        return 0

    print(f"companies with at least {args.min_bids} appearances: {len(rows)}")
    print(f"\n{'company':<38} {'seen':>5} {'won':>4} {'rate':>6} "
          f"{'value QAR':>16}")
    print("-" * 74)
    for r in rows[:args.limit]:
        rate = r["won"] / r["appearances"] if r["appearances"] else 0
        print(f"{(r['name'] or '')[:37]:<38} {r['appearances']:>5} "
              f"{r['won']:>4} {rate:>5.0%} {(r['value'] or 0):>16,.0f}")

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


def cmd_export(args) -> int:
    store = Store(args.db)
    sql = {
        "awards": "SELECT c.*, t.family, t.state, t.awarded_date, t.ministry,"
                  " t.awarded_amount FROM company c JOIN tender t"
                  " USING (tender_id)",
        "tenders": "SELECT * FROM tender",
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
    """Walk every section until the store is complete, or the clock runs out.

    Each page is committed as it is fetched, so closing the laptop (or a
    --max-hours cut-off) never loses progress. A nightly scheduled task
    with --max-hours 6 finishes a first load over several nights, then
    later runs only pick up new tenders and state changes (open → awarded).
    """
    _set_deadline(args)
    kinds = list(COMPANY_KINDS) + [k for k in LISTING_KINDS
                                   if k not in COMPANY_KINDS]
    deadline = getattr(args, "deadline", None)

    print("=== classified register ===")
    cmd_companies(argparse.Namespace(
        db=args.db, pages=None,
        full=bool(getattr(args, "full_register", False)),
        delay=args.delay, timeout=args.timeout,
        max_hours=None, deadline=deadline,
    ))
    if _expired(args):
        print("\ntime budget reached. Re-run harvest to continue.")
        cmd_status(argparse.Namespace(db=args.db, runs=5))
        return 0

    for kind in kinds:
        if _expired(args):
            print("\ntime budget reached. Re-run harvest to continue.")
            break
        print(f"\n=== {kind} ===")
        cmd_crawl(argparse.Namespace(
            db=args.db, kind=kind, pages=None, full=False, stop_after=2,
            limit_details=None, delay=args.delay, timeout=args.timeout,
            listings_only=False, no_details=args.no_details,
            max_hours=None, deadline=deadline,
        ))

    cmd_status(argparse.Namespace(db=args.db, runs=8))
    store = Store(args.db)
    done = {r["kind"]: r["backfill_complete"]
            for r in store.conn.execute(
                "SELECT kind, backfill_complete FROM section_state")}
    store.close()
    wanted = ["classified", *kinds]
    missing = [k for k in wanted if not done.get(k)]
    if missing:
        print(f"\nstill to finish: {', '.join(missing)}")
        print("Re-run harvest (or the scheduled task) until this list is empty.")
        return 0
    print("\nbackfill complete. Later runs only fetch new and changed tenders.")
    return 0


def cmd_profile(args) -> int:
    store = Store(args.db)
    rows = store.conn.execute(
        "SELECT m.role, m.method, m.score, m.matched_name, m.matched_cr,"
        " t.tender_number, t.ministry, t.family, t.state, t.awarded_date,"
        " t.closing_date, c.value, t.awarded_amount, t.local_value_system"
        " FROM match m JOIN tender t USING (tender_id)"
        " JOIN company c ON c.tender_id=m.tender_id AND c.role=m.role"
        "                AND c.seq=m.seq"
        " WHERE m.customer_id=?"
        " ORDER BY COALESCE(t.awarded_date, t.closing_date) DESC",
        (args.customer,)).fetchall()

    crs = {r["matched_cr"] for r in rows if r["matched_cr"]}
    reg = None
    for cr in crs:
        from .normalize import cr_root
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
        print(f"  classified      : {reg['size'] or '-'}, evaluation "
              f"{reg['evaluation'] or '-'}, certificate to "
              f"{reg['certificate_end'] or '-'}")
        for a in store.conn.execute(
                "SELECT activity_code, activity_name, grade"
                " FROM company_activity WHERE profile_number=? LIMIT 6",
                (reg["profile_number"],)).fetchall():
            print(f"    {a['activity_code']:<10} {a['grade'] or '-':<10} "
                  f"{(a['activity_name'] or '')[:40]}")

    won = [r for r in rows if r["role"] == "awarded"]
    seen = {r["tender_number"] for r in rows}
    value = sum(r["value"] or 0 for r in won)
    print(f"  tenders seen in : {len(seen)}")
    print(f"  awards won      : {len(won)}")
    print(f"  awarded value   : {value:,.2f} QAR")
    if seen:
        print(f"  win rate        : {len(won) / len(seen):.0%}")
    entities = {r["ministry"] for r in won if r["ministry"]}
    if entities and value:
        top = max(entities, key=lambda e: sum(
            r["value"] or 0 for r in won if r["ministry"] == e))
        share = sum(r["value"] or 0 for r in won
                    if r["ministry"] == top) / value
        print(f"  awarding bodies : {len(entities)}"
              f"  (largest {top}, {share:.0%} of value)")

    print(f"\n  {'date':<11} {'state':<10} {'role':<8} {'value':>14}  body")
    for r in rows[:30]:
        when = r["awarded_date"] or r["closing_date"] or "-"
        print(f"  {when:<11} {r['state'] or '-':<10} {r['role']:<8} "
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
        "crawl", help="fetch new and changed tenders (incremental)",
        description="Default: read the newest listing pages until reaching "
                    "tenders already stored, finish any interrupted first "
                    "load, then fetch detail pages only for tenders that are "
                    "new or whose state has moved on. Safe to run as often "
                    "as you like.")
    db(sp)
    sp.add_argument("--kind", default="awarded", help=kind_help)
    sp.add_argument("--pages", help="restrict to a range, e.g. 1-100")
    sp.add_argument("--full", action="store_true",
                    help="read every listing page instead of stopping when "
                         "caught up. Details are still only fetched if needed.")
    sp.add_argument("--stop-after", type=int, default=2,
                    help="consecutive already-known pages that mean 'caught "
                         "up' (default 2)")
    sp.add_argument("--limit-details", type=int,
                    help="fetch at most this many detail pages this run")
    sp.add_argument("--delay", type=float, default=1.5)
    sp.add_argument("--timeout", type=float, default=90.0,
                    help="seconds to wait for a slow page (default 90)")
    sp.add_argument("--listings-only", action="store_true")
    sp.add_argument("--no-details", action="store_true",
                    help="skip TenderDetails pages: faster, less detail")
    sp.add_argument("--max-hours", type=float,
                    help="stop cleanly after this many hours; re-run to continue")
    sp.set_defaults(func=cmd_crawl)

    sp = sub.add_parser(
        "companies", help="crawl the classified register (incremental)",
        description="Default: fetch only the pages new companies land on, "
                    "plus any that failed before. --full refreshes every "
                    "company, which picks up changed evaluations and "
                    "certificate dates -- about 7 minutes, worth doing "
                    "monthly.")
    db(sp)
    sp.add_argument("--pages")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--delay", type=float, default=1.5)
    sp.add_argument("--timeout", type=float, default=90.0)
    sp.add_argument("--max-hours", type=float)
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
    sp.add_argument("--timeout", type=float, default=90.0)
    sp.add_argument("--max-hours", type=float, default=6.0,
                    help="stop cleanly after this many hours (default 6)")
    sp.add_argument("--no-details", action="store_true")
    sp.add_argument("--full-register", action="store_true",
                    help="refresh every classified company, not just new ones")
    sp.set_defaults(func=cmd_harvest)

    sp = sub.add_parser("status", help="progress, pending retries, recent runs")
    db(sp)
    sp.add_argument("--runs", type=int, default=8)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("parse", help="re-parse stored HTML, no network")
    db(sp)
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("match", help="join your customers to the data")
    db(sp)
    sp.add_argument("--customers", required=True,
                    help=".xlsx or .csv -- customer_id, name, cr_number "
                         "(common variants of those headers are recognised)")
    sp.add_argument("--sheet", help="worksheet name, for .xlsx (default first)")
    sp.add_argument("--fuzzy", type=float, default=0.90)
    sp.add_argument("--replace", action="store_true")
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

    sp = sub.add_parser("export", help="dump a table to CSV")
    db(sp)
    sp.add_argument("--table", default="awards",
                    choices=["awards", "tenders", "companies", "activities",
                             "tender_activities", "matches", "features"])
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
