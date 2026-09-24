"""CR-by-month features from awards and bids.

Point-in-time: a feature dated ``as_of`` only uses tenders whose event date
is on or before ``as_of``. The event date is ``awarded_date``, falling back
to ``closing_date``. Classified-register fields are a current snapshot, not
a history -- they are attached as of the latest month only.

v2 (Sept 2026 fixes):
  - an award's value is the sum of the company's Approved Value lines on
    that tender (v1 took the smallest line, and fell back to the proposal
    amount);
  - win rate is wins / decided tenders, where decided means the winners are
    known. v1 divided by every appearance, so bids still under evaluation
    counted as losses (one win + three pending bids gave 0.25, not 1.0).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from .store import Store, _now

FEATURE_VERSION = "v2"


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

    # Decided = the winners are known: any awarded row, with or without a
    # CR. A tender still in technical or financial evaluation, or
    # cancelled, has none and is neither a win nor a loss for anyone on it.
    decided_tenders = {r[0] for r in store.conn.execute(
        "SELECT DISTINCT tender_id FROM company WHERE role='awarded'")}

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
        decided = tender_id in decided_tenders
        awarded_value: dict[str, float] = defaultdict(float)
        has_award_value: set[str] = set()
        for r, _ in entries:
            if r["role"] == "awarded" and r["value"] is not None:
                awarded_value[r["cr_root"]] += r["value"]
                has_award_value.add(r["cr_root"])
        crs = {r["cr_root"] for r, _ in entries}
        ministry = next((r["ministry"] for r, _ in entries if r["ministry"]), None)
        for cr in crs:
            history[cr].append({
                "information_date": event,
                "won": cr in winners,
                "decided": decided,
                "own_amount": (round(awarded_value[cr], 2)
                               if cr in has_award_value else None),
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
    decided_12m = [p for p in window_12m if p["decided"]]
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
        "decided_12m_count": len(decided_12m),
        "wins_12m_count": len(wins_12m),
        "win_rate_12m": (
            round(len(wins_12m) / len(decided_12m), 4)
            if len(decided_12m) >= 3 else None),
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


# --------------------------------------------------------------------------
# client-by-month features
#
# feature_cr_month above is keyed by commercial registration number: it says
# what a company has been doing. This is keyed by QDB client, and adds the
# part the transaction model contributes -- what the borrower's own top
# buyers and suppliers have been doing, which is a read on the demand behind
# its receivables and the solidity of its supply.
#
# The same point-in-time rule holds throughout: a row dated as_of_month uses
# no event after that month end.
# --------------------------------------------------------------------------

CLIENT_FEATURE_VERSION = "c1"


def _window(events, lo, hi):
    return [e for e in events if lo < e["date"] <= hi]


def _client_month(client_id, as_of, events):
    """One (client, month) row from that client's matched events."""
    seen = [e for e in events if e["date"] <= as_of]
    if not seen:
        return None
    y12 = date(as_of.year - 1, as_of.month, 1)
    y36 = date(as_of.year - 3, as_of.month, 1)

    own = [e for e in seen if e["relation"] == "self"]
    own_12 = _window(own, y12, as_of)
    own_36 = _window(own, y36, as_of)

    # A tender is a win or a loss only once its winners are known. One still
    # under evaluation is neither, so it is left out of the denominator.
    bids_12 = {e["tender_id"] for e in own_12}
    decided_12 = {e["tender_id"] for e in own_12 if e["decided"]}
    wins_12 = {e["tender_id"] for e in own_12 if e["won"]}

    def award_value(rows):
        # Several approved-value lines for one company on one tender are
        # separate match rows; they are that company's award between them.
        return sum(e["value"] for e in rows
                   if e["won"] and e["value"] is not None)

    per_tender: dict[str, float] = defaultdict(float)
    for e in own_12:
        if e["won"] and e["value"] is not None:
            per_tender[e["tender_id"]] += e["value"]
    buyer_value: dict[str, float] = defaultdict(float)
    for e in own_12:
        if e["won"] and e["buyer"] and e["value"] is not None:
            buyer_value[e["buyer"]] += e["value"]

    wins_all = [e for e in own if e["won"]]
    last_award = max((e["date"] for e in wins_all), default=None)
    first_seen = min(e["date"] for e in seen)

    def side(relation):
        rows = [e for e in seen if e["relation"] == relation]
        rows_12 = _window(rows, y12, as_of)
        return {
            "matched": len({e["party_key"] for e in rows}),
            "active": len({e["party_key"] for e in rows_12 if e["won"]}),
            "value": award_value(rows_12) or None,
            "rows_12": rows_12,
        }

    buyers, suppliers = side("buyer"), side("supplier")
    cp_rows = buyers["rows_12"] + suppliers["rows_12"]
    review = sum(1 for e in cp_rows if e["needs_review"])

    return {
        "client_id": client_id,
        "as_of_month": as_of.isoformat(),
        "self_bids_12m": len(bids_12),
        "self_decided_12m": len(decided_12),
        "self_wins_12m": len(wins_12),
        "self_win_rate_12m": (round(len(wins_12) / len(decided_12), 4)
                              if len(decided_12) >= 3 else None),
        "self_award_value_12m": award_value(own_12) or None,
        "self_award_value_36m": award_value(own_36) or None,
        "self_largest_award_12m": (max(per_tender.values())
                                   if per_tender else None),
        "self_months_since_last_award": (_months_between(last_award, as_of)
                                         if last_award else None),
        "self_distinct_buyers_12m": len({e["buyer"] for e in own_12
                                         if e["won"] and e["buyer"]}),
        "self_top_buyer_share_12m": (
            round(max(buyer_value.values()) / sum(buyer_value.values()), 4)
            if buyer_value and sum(buyer_value.values()) > 0 else None),
        "self_months_since_first_seen": _months_between(first_seen, as_of),
        "buyers_matched": buyers["matched"],
        "buyers_active_12m": buyers["active"],
        "buyer_award_value_12m": buyers["value"],
        "suppliers_matched": suppliers["matched"],
        "suppliers_active_12m": suppliers["active"],
        "supplier_award_value_12m": suppliers["value"],
        # How much of the counterparty signal rests on a name alone. A high
        # share means these numbers are a lead to check, not evidence.
        "counterparty_review_share": (round(review / len(cp_rows), 4)
                                      if cp_rows else None),
        "feature_version": CLIENT_FEATURE_VERSION,
        "built_at": _now(),
    }


def build_client_months(store: Store, months: int = 60,
                        as_of_end: date | None = None) -> dict[str, int]:
    """Rebuild ``client_month`` for the trailing ``months`` window."""
    by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in store.match_rows():
        when = _parse_iso(r["event_date"])
        if when is None:
            continue
        by_client[r["client_id"]].append({
            "date": when,
            "tender_id": r["tender_id"],
            "relation": r["relation"],
            "party_key": (r["party_name"] or "").upper()
                         if r["relation"] != "self" else "",
            "won": bool(r["won"]),
            "decided": bool(r["decided"]),
            "value": r["company_value"],
            "buyer": r["buyer"],
            "needs_review": bool(r["needs_review"]),
        })

    last = as_of_end or date.today()
    last = month_end(last.year, last.month)
    written = 0
    store.conn.execute("DELETE FROM client_month")
    for client_id, events in by_client.items():
        first = min(e["date"] for e in events)
        start = max(first, date(last.year, last.month, 1) -
                    timedelta(days=31 * months))
        rows = []
        for as_of in _month_sequence(start, last):
            rec = _client_month(client_id, as_of, events)
            if rec:
                rows.append(rec)
        written += store.save_client_months(rows)
    return {"clients": len(by_client), "rows": written}
