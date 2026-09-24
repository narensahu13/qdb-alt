"""What the ICV mirror contributes to a model.

A single score is almost useless on its own: 24% means nothing until you know
it was 38% two years ago, that the sector median is 31%, and that the last
move was a rule change rather than the company doing anything.

So everything here is a trajectory read *as at a date*, and every figure is
built only from observations published on or before that date. The one rule
that matters: a row dated a month never uses an event after it.

Policy and performance are kept apart throughout. QatarEnergy's transitional
percentage was added to thousands of scores at once in late 2024; a model fed
raw deltas would learn that date rather than anything about a borrower.
"""

from __future__ import annotations

import calendar
import statistics
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .store import Store, today

FEATURE_VERSION = "i1"

# Reasons that are a change in the rules, not in the company. Matched after
# stripping, because the portal's own labels carry trailing spaces.
POLICY_REASONS = {"Transitional Percentage Added"}


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _parse(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _month_sequence(first: date, last: date) -> list[date]:
    out: list[date] = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(month_end(y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _now() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def score_profile(observations: Iterable[dict[str, Any]],
                  as_of: date) -> dict[str, Any]:
    """The trajectory of one company's score, as at ``as_of``.

    ``observations`` is that company's published series; anything posted
    after ``as_of`` is ignored rather than trusted.
    """
    seen = sorted((o for o in observations if o["date"] <= as_of),
                  key=lambda o: o["date"])
    if not seen:
        # Not zero, and not today's score: the company held no certificate
        # on that date. A model must be able to tell those apart.
        return {
            "icv_score": None, "icv_score_date": None, "icv_score_reason": None,
            "icv_first_certificate": None, "icv_observations": 0,
            "icv_months_since_last_change": None,
            "icv_score_12m_ago": None, "icv_score_change_12m": None,
            "icv_organic_change_12m": None, "icv_policy_change_12m": None,
            "icv_months_certified": None,
        }
    latest = seen[-1]
    cutoff = date(as_of.year - 1, as_of.month, 1)
    window = [o for o in seen if o["date"] > cutoff]
    before = [o for o in seen if o["date"] <= cutoff]
    prior = before[-1]["score"] if before else None

    def moved(rows, policy: bool) -> float | None:
        vals = [o["change"] for o in rows
                if o["change"] is not None
                and ((o["reason"] or "").strip() in POLICY_REASONS) is policy]
        return round(sum(vals), 2) if vals else None

    return {
        "icv_score": latest["score"],
        "icv_score_date": latest["date"].isoformat(),
        "icv_score_reason": latest["reason"],
        "icv_first_certificate": seen[0]["date"].isoformat(),
        "icv_observations": len(seen),
        "icv_months_since_last_change": _months_between(latest["date"], as_of),
        "icv_score_12m_ago": prior,
        "icv_score_change_12m": (round(latest["score"] - prior, 2)
                                 if prior is not None else None),
        # The part of the last year's movement the company actually earned,
        # and the part that was handed to everyone at once.
        "icv_organic_change_12m": moved(window, policy=False),
        "icv_policy_change_12m": moved(window, policy=True),
        "icv_months_certified": _months_between(seen[0]["date"], as_of),
    }


def _observations(store: Store) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in store.conn.execute(
            "SELECT company_id, posted_date, score, change, reason"
            " FROM score_observation ORDER BY company_id, posted_date"):
        when = _parse(r["posted_date"])
        if when is None:
            continue
        out.setdefault(r["company_id"], []).append({
            "date": when, "score": r["score"], "change": r["change"],
            "reason": r["reason"]})
    return out


def _industry_of(store: Store) -> dict[str, str | None]:
    return {r["id"]: r["industry"] for r in store.conn.execute(
        "SELECT id, industry FROM company")}


def cohort_medians(store: Store, as_of: date,
                   observations: dict[str, list[dict[str, Any]]] | None = None,
                   industries: dict[str, str | None] | None = None
                   ) -> dict[str, float]:
    """The median score of each industry as at a date.

    A borrower is read against its own sector at the same date, never
    against a fixed threshold: the sectors sit at different levels and all
    of them moved when the transitional percentage landed.
    """
    observations = observations if observations is not None else _observations(store)
    industries = industries if industries is not None else _industry_of(store)
    by_industry: dict[str, list[float]] = {}
    for company_id, obs in observations.items():
        industry = industries.get(company_id)
        if not industry:
            continue
        seen = [o for o in obs if o["date"] <= as_of]
        if seen:
            by_industry.setdefault(industry, []).append(
                max(seen, key=lambda o: o["date"])["score"])
    return {k: round(statistics.median(v), 2) for k, v in by_industry.items() if v}


def standing(store: Store, company_id: str, as_of: date) -> dict[str, Any]:
    """Status and certificate, as at a date.

    Only knowable from the day the sweeps started: the portal publishes the
    score history but not the standing history, so before the first sweep
    these are empty and that is the honest answer.
    """
    row = store.status_as_at(company_id, as_of.isoformat())
    if row is None:
        return {"icv_status": None, "icv_expiry_date": None,
                "icv_certificate_no": None, "icv_days_to_expiry": None,
                "icv_total_score_listed": None, "icv_standing_known": 0}
    expiry = _parse(row["expiry_date"])
    return {
        "icv_status": row["status"],
        "icv_expiry_date": row["expiry_date"],
        "icv_certificate_no": row["certificate_no"],
        "icv_days_to_expiry": (expiry - as_of).days if expiry else None,
        "icv_total_score_listed": row["total_score"],
        "icv_standing_known": 1,
    }


def client_rows(store: Store, as_of: date | None = None) -> list[dict[str, Any]]:
    """One row per matched (client, supplier), with everything as at a date."""
    as_of = as_of or date.today()
    obs = _observations(store)
    industries = _industry_of(store)
    medians = cohort_medians(store, as_of, obs, industries)
    out = []
    for m in store.conn.execute(
            "SELECT m.*, c.name, c.cr_number, c.industry FROM match m"
            " JOIN company c ON c.id = m.company_id"
            " ORDER BY m.customer_id"):
        row = {
            "client_id": m["customer_id"],
            "relation": "self",
            "company_id": m["company_id"],
            "matched_name": m["name"],
            "matched_cr": m["cr_number"],
            "match_method": m["method"],
            "match_confidence": m["confidence"],
            "needs_review": int(m["method"] in ("name_fuzzy", "name_translit")),
            "industry": m["industry"],
            "as_of": as_of.isoformat(),
        }
        row.update(score_profile(obs.get(m["company_id"], []), as_of))
        row.update(standing(store, m["company_id"], as_of))
        median = medians.get(m["industry"] or "")
        row["icv_industry_median"] = median
        row["icv_vs_industry"] = (
            round(row["icv_score"] - median, 2)
            if median is not None and row["icv_score"] is not None else None)
        out.append(row)
    return out


def build_client_months(store: Store, months: int = 60,
                        as_of_end: date | None = None) -> dict[str, int]:
    """Rebuild ``client_month``: one row per matched client per month."""
    obs = _observations(store)
    industries = _industry_of(store)
    pairs = [(r["customer_id"], r["company_id"]) for r in store.conn.execute(
        "SELECT customer_id, company_id FROM match")]
    if not pairs:
        return {"clients": 0, "rows": 0}

    last = as_of_end or date.today()
    last = month_end(last.year, last.month)
    earliest = min((o["date"] for series in obs.values() for o in series),
                   default=last)
    start = max(earliest, month_end(last.year - months // 12, last.month))
    sequence = _month_sequence(start, last)
    medians = {m: cohort_medians(store, m, obs, industries) for m in sequence}

    store.conn.execute("DELETE FROM client_month")
    rows = []
    for client_id, company_id in pairs:
        series = obs.get(company_id, [])
        for as_of in sequence:
            prof = score_profile(series, as_of)
            if prof["icv_score"] is None:
                continue          # not certified yet: no row, not a zero
            stand = standing(store, company_id, as_of)
            median = medians[as_of].get(industries.get(company_id) or "")
            rows.append({
                "client_id": client_id,
                "as_of_month": as_of.isoformat(),
                "company_id": company_id,
                "industry": industries.get(company_id),
                "score": prof["icv_score"],
                "score_date": prof["icv_score_date"],
                "score_change_12m": prof["icv_score_change_12m"],
                "organic_change_12m": prof["icv_organic_change_12m"],
                "policy_change_12m": prof["icv_policy_change_12m"],
                "months_since_last_change": prof["icv_months_since_last_change"],
                "months_certified": prof["icv_months_certified"],
                "observations": prof["icv_observations"],
                "status": stand["icv_status"],
                "days_to_expiry": stand["icv_days_to_expiry"],
                "standing_known": stand["icv_standing_known"],
                "industry_median": median,
                "vs_industry": (round(prof["icv_score"] - median, 2)
                                if median is not None else None),
                "feature_version": FEATURE_VERSION,
                "built_at": _now(),
            })
    written = store.save_client_months(rows)
    return {"clients": len({p[0] for p in pairs}), "rows": written}
