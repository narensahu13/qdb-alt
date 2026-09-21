"""CR-by-month features from awards and bids.

Point-in-time: a feature dated ``as_of`` only uses tenders whose event date
is on or before ``as_of``. The event date is ``awarded_date``, falling back
to ``closing_date``. Classified-register fields are a current snapshot, not
a history -- they are attached as of the latest month only.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from .store import Store, _now

FEATURE_VERSION = "v1"


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_sequence(first: date, last: date) -> list[date]:
    months: list[date] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(month_end(year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _parse_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _sum_or_none(values: list[float]) -> float | None:
    return round(sum(values), 2) if values else None


def build_features(store: Store, months: int = 60,
                   as_of_end: date | None = None) -> dict[str, int]:
    """Rebuild ``feature_cr_month`` for the trailing ``months`` window."""
    rows = store.conn.execute(
        "SELECT c.cr_root, c.role, c.value, t.tender_id, t.ministry,"
        " t.awarded_date, t.closing_date"
        " FROM company c JOIN tender t USING (tender_id)"
        " WHERE c.cr_root IS NOT NULL"
    ).fetchall()

    by_tender: dict[str, list] = defaultdict(list)
    for r in rows:
        event = _parse_iso(r["awarded_date"]) or _parse_iso(r["closing_date"])
        if event is None:
            continue
        by_tender[r["tender_id"]].append((r, event))

    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tender_id, entries in by_tender.items():
        event = entries[0][1]
        winners = {r["cr_root"] for r, _ in entries if r["role"] == "awarded"}
        amounts: dict[str, float] = {}
        for r, _ in entries:
            if r["value"] is None:
                continue
            current = amounts.get(r["cr_root"])
            if current is None or r["value"] < current:
                amounts[r["cr_root"]] = r["value"]
        crs = {r["cr_root"] for r, _ in entries}
        ministry = next((r["ministry"] for r, _ in entries if r["ministry"]), None)
        for cr in crs:
            rest = [v for other, v in amounts.items() if other != cr]
            history[cr].append({
                "information_date": event,
                "won": cr in winners,
                "own_amount": amounts.get(cr),
                "best_other": min(rest) if rest else None,
                "ministry": ministry,
            })

    if not history:
        store.replace_features([])
        return {"crs": 0, "rows": 0, "months": 0}

    classified = {
        r["cr_root"]: r
        for r in store.conn.execute(
            "SELECT cr_root, size, evaluation, certificate_end"
            " FROM classified_company WHERE cr_root IS NOT NULL"
        )
    }

    end = as_of_end or date.today()
    end_month = month_end(end.year, end.month)
    earliest = min(p["information_date"] for events in history.values() for p in events)
    start_year, start_month = end_month.year, end_month.month
    for _ in range(max(months - 1, 0)):
        start_year, start_month = (
            (start_year - 1, 12) if start_month == 1 else (start_year, start_month - 1)
        )
    start = max(month_end(start_year, start_month),
                month_end(earliest.year, earliest.month))
    grid = _month_sequence(start, end_month)

    out: list[dict[str, Any]] = []
    for cr, events in history.items():
        for as_of in grid:
            rec = _features_for(cr, as_of, events, classified.get(cr),
                                snapshot=(as_of == grid[-1]))
            if rec is not None:
                out.append(rec)

    store.replace_features(out)
    return {"crs": len(history), "rows": len(out), "months": len(grid)}


def _features_for(cr_root: str, as_of: date, events: list[dict[str, Any]],
                  classified, snapshot: bool) -> dict[str, Any] | None:
    visible = [p for p in events if p["information_date"] <= as_of]
    if not visible:
        return None

    cutoff_12m = as_of - timedelta(days=365)
    cutoff_36m = as_of - timedelta(days=365 * 3)
    window_12m = [p for p in visible if p["information_date"] > cutoff_12m]
    wins_12m = [p for p in window_12m if p["won"]]
    wins_36m = [p for p in visible if p["won"] and p["information_date"] > cutoff_36m]
    wins_all = [p for p in visible if p["won"]]
    values_12m = [p["own_amount"] for p in wins_12m if p["own_amount"] is not None]

    buyer_values: dict[str, float] = defaultdict(float)
    for p in wins_12m:
        if p["own_amount"] is not None:
            buyer_values[p["ministry"] or "(unknown)"] += p["own_amount"]

    last_award = max((p["information_date"] for p in wins_all), default=None)
    first_seen = min(p["information_date"] for p in visible)

    rec = {
        "cr_root": cr_root,
        "as_of_month": as_of.isoformat(),
        "awards_12m_count": len(wins_12m),
        "awards_12m_value": _sum_or_none(values_12m),
        "awards_36m_count": len(wins_36m),
        "awards_36m_value": _sum_or_none(
            [p["own_amount"] for p in wins_36m if p["own_amount"] is not None]),
        "awards_life_value": _sum_or_none(
            [p["own_amount"] for p in wins_all if p["own_amount"] is not None]),
        "largest_award_12m": max(values_12m) if values_12m else None,
        "months_since_last_award": (
            _months_between(last_award, as_of) if last_award else None),
        "bids_12m_count": len(window_12m),
        "wins_12m_count": len(wins_12m),
        "win_rate_12m": (
            round(len(wins_12m) / len(window_12m), 4) if len(window_12m) >= 3 else None),
        "distinct_buyers_12m": len({p["ministry"] for p in wins_12m if p["ministry"]}),
        "top_buyer_share_12m": (
            round(max(buyer_values.values()) / sum(buyer_values.values()), 4)
            if buyer_values and sum(buyer_values.values()) > 0 else None),
        "months_since_first_seen": _months_between(first_seen, as_of),
        "classified_size": classified["size"] if classified and snapshot else None,
        "classified_evaluation": (
            classified["evaluation"] if classified and snapshot else None),
        "certificate_end": (
            classified["certificate_end"] if classified and snapshot else None),
        "feature_version": FEATURE_VERSION,
        "built_at": _now(),
    }
    return rec
