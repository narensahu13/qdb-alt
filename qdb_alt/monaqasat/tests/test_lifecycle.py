"""Tenders moving between sections, against the simulated site.

Each test reproduces a way the live site behaves (checked Sept 2026) that
the first qdb-alt version got wrong or never exercised: sections that are
not in entry order, sections that overlap, awards published with an earlier
date, pages answered with the home page, and so on.
"""

import unittest
from unittest import mock

from monaqasat import cli

try:
    from test_incremental import PER_PAGE, Harness, _args, award_date
except ImportError:                                   # run as tests.test_...
    from tests.test_incremental import (PER_PAGE, Harness, _args,
                                        award_date)

AVAIL = "AvailableMinistriesTenders"
CLOSED = "ClosedTenders"
TECH = "TechnicallyOpenedTenders"
FIN = "FinanciallyOpenedTenders"
AWARD = "AwardedTenders"
CANC = "CancelledTenders"


class OpenToClosed(Harness):
    """Closed is in publish-date order, so a tender that closes today lands
    where its publish date puts it -- page 4 here, not page 1."""

    def setUp(self):
        super().setUp()
        self.site.publish(CLOSED, 200)                     # 10 pages
        self.avail = self.site.publish(AVAIL, 40)
        self.crawl(kind="available,closed")
        self.t = self.avail[25]
        self.site.sections[AVAIL].remove(self.t)
        self.site.sections[CLOSED].insert(70, self.t)      # page 4

    def test_the_move_is_found_wherever_it_lands(self):
        self.crawl(kind="available,closed")
        s = self.store()
        self.assertEqual(s.current_state(self.t), "closed")
        s.close()

    def test_nothing_is_deleted_and_the_old_listing_is_marked_gone(self):
        self.crawl(kind="available,closed")     # first miss
        s = self.store()
        row = s.conn.execute("SELECT * FROM sighting WHERE tender_id=? AND"
                             " kind='available'", (self.t,)).fetchone()
        self.assertIsNone(row["gone_at"], "one miss is not enough")
        self.assertEqual(row["missed"], 1)
        s.close()
        self.crawl(kind="available,closed")     # second miss
        s = self.store()
        rows = {r["kind"]: r for r in s.conn.execute(
            "SELECT * FROM sighting WHERE tender_id=?", (self.t,))}
        self.assertEqual(set(rows), {"available", "closed"})
        self.assertIsNotNone(rows["available"]["gone_at"])
        self.assertIsNone(rows["closed"]["gone_at"])
        self.assertEqual(s.stats()["tenders"], 240)
        s.close()

    def test_quick_mode_is_the_old_behaviour_and_misses_it(self):
        self.crawl(kind="available,closed", quick=True)
        s = self.store()
        self.assertEqual(s.current_state(self.t), "published")
        s.close()


class Reopened(Harness):
    def test_a_closing_date_extended_after_closing(self):
        ids = self.site.publish(CLOSED, 5)
        self.crawl(kind="available,closed")
        t = ids[0]
        self.site.sections[CLOSED].remove(t)
        self.site.sections.setdefault(AVAIL, []).insert(0, t)
        self.crawl(kind="available,closed")
        s = self.store()
        # still listed under Closed as far as we know: one miss only
        self.assertEqual(s.current_state(t), "closed")
        s.close()
        self.crawl(kind="available,closed")
        s = self.store()
        self.assertEqual(s.current_state(t), "published")
        s.close()

    def test_an_opened_tender_never_goes_back(self):
        ids = self.site.publish(TECH, 5)
        self.crawl(kind="technical")
        t = ids[0]
        self.site.sections[TECH].remove(t)
        self.site.sections.setdefault(AVAIL, []).insert(0, t)
        for _ in range(3):
            self.crawl(kind="available,technical")
        s = self.store()
        self.assertEqual(s.current_state(t), "technical")
        s.close()


class OverlappingSections(Harness):
    """Every Financially Opened tender is also listed under Technically
    Opened. Listings are read before detail pages, so the state is settled
    first and the companies page is fetched once."""

    def test_state_is_the_furthest_and_one_fetch(self):
        self.site.publish(TECH, 40)
        self.site.sections[FIN] = list(self.site.sections[TECH][:15])
        self.crawl(kind="technical,financial")
        both = self.site.sections[FIN][0]
        s = self.store()
        self.assertEqual(s.current_state(both), "financial")
        s.close()
        fetched = [r for r in self.site.requests
                   if "TenderCompaniesDetails/" in r]
        self.assertEqual(len(fetched), 40)
        self.assertEqual(len(set(fetched)), 40)

    def test_financial_opening_does_not_refetch(self):
        """At financial opening the live page still shows the technical
        list only, so reading it again adds nothing."""
        ids = self.site.publish(TECH, 5)
        self.crawl(kind="technical,financial")
        self.site.sections[FIN] = [ids[0]]
        self.crawl(kind="technical,financial")
        self.assertEqual(self.site.count(f"TenderCompaniesDetails/{ids[0]}"), 0)

    def test_award_refetches_and_keeps_every_bidder(self):
        ids = self.site.publish(TECH, 5)
        self.crawl(kind="technical,awarded")
        t = ids[0]
        self.site.sections[TECH].remove(t)
        self.site.sections.setdefault(AWARD, []).insert(0, t)
        self.crawl(kind="technical,awarded")
        s = self.store()
        rows = s.conn.execute("SELECT role, name FROM company WHERE"
                              " tender_id=? ORDER BY role, seq", (t,)).fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [("awarded", f"CO {t}"), ("bidder", f"CO {t}"),
                          ("bidder", f"OTHER {t}")])
        self.assertEqual(s.current_state(t), "awarded")
        s.close()


class AwardedHeadScan(Harness):
    """Awarded is newest award date first. An award can be published days
    after the date it carries, so it lands below page 1."""

    def setUp(self):
        super().setUp()
        self.ids = self.site.publish(AWARD, 400)           # 20 pages
        self.crawl()

    def _backdate(self, days):
        """A new award, dated `days` before the newest one held, placed
        where that date sorts."""
        newest = self.site.sections[AWARD][0]
        t = self.site.publish("Staging", 1)[0]
        self.site.dates[t] = award_date(int(newest) - days)
        self.site.sections[AWARD].insert(days, t)
        return t

    def test_backdated_within_the_lookback_is_found_at_once(self):
        t = self._backdate(40)          # page 3, dated 40 days back
        self.crawl(lookback_days=60)
        s = self.store()
        self.assertEqual(s.current_state(t), "awarded")
        s.close()

    def test_backdated_beyond_the_lookback_is_found_by_the_rolling_pass(self):
        t = self._backdate(200)         # page 11
        self.crawl(lookback_days=14)
        s = self.store()
        self.assertIsNone(s.current_state(t), "head scan stops before it")
        s.close()
        # a rolling pass starts once --sweep-days have passed; force it.
        # Slices overlap by a page: 2-6, 6-10, 10-14.
        for _ in range(3):
            self.crawl(sweep_days=0, roll_pages=5)
        s = self.store()
        self.assertEqual(s.current_state(t), "awarded")
        s.close()

    def test_rolling_pass_is_spread_over_runs_and_finishes(self):
        pages = []
        for _ in range(5):
            self.crawl(sweep_days=0, roll_pages=5)
            pages.append(self.site.count("AwardedTenders/"))
        self.assertTrue(all(n <= 9 for n in pages), pages)
        s = self.store()
        self.assertIsNone(s.section("awarded")["roll_next"])
        self.assertIsNotNone(s.section("awarded")["last_full_pass"])
        s.close()

    def test_same_day_awards_interleaved_with_known_ones(self):
        """Thirty awards on one date; ten new ones published among them."""
        same = award_date(int(self.site.sections[AWARD][0]) + 1)
        block = self.site.publish(AWARD, 30)
        for t in block:
            self.site.dates[t] = same
        self.crawl()
        new = self.site.publish("Staging", 10)
        for i, t in enumerate(new):
            self.site.dates[t] = same
            self.site.sections[AWARD].insert(3 * i + 1, t)
        self.crawl()
        s = self.store()
        self.assertTrue(all(s.current_state(t) == "awarded" for t in new))
        s.close()


class CancelledIsNotInDateOrder(Harness):
    def test_a_cancellation_deep_in_the_list_is_found_by_the_rolling_pass(self):
        self.site.publish(CANC, 200)                       # 10 pages
        ids = self.site.publish(TECH, 5)
        self.crawl(kind="technical,cancelled")
        t = ids[0]
        self.site.sections[TECH].remove(t)
        self.site.sections[CANC].insert(150, t)            # page 8
        self.crawl(kind="technical,cancelled")
        s = self.store()
        self.assertEqual(s.current_state(t), "technical")
        s.close()
        self.crawl(kind="technical,cancelled", sweep_days=0, roll_pages=10)
        s = self.store()
        self.assertEqual(s.current_state(t), "cancelled")
        # and while it was unplaced, status could say so
        s.close()

    def test_a_tender_between_sections_is_reported(self):
        ids = self.site.publish(TECH, 5)
        self.site.publish(CANC, 200)
        self.crawl(kind="technical,cancelled")
        t = ids[0]
        self.site.sections[TECH].remove(t)
        self.site.sections[CANC].insert(150, t)
        self.crawl(kind="technical,cancelled")
        self.crawl(kind="technical,cancelled")
        s = self.store()
        self.assertEqual(s.left_every_list(), [t])
        s.close()


class DetailPages(Harness):
    def test_home_page_answers_do_not_stall_the_queue(self):
        """Six TenderDetails pages answered with the home page used to stop
        the detail phase before any companies page for five nights."""
        ids = self.site.publish(AWARD, 60)
        self.site.home |= set(ids[-6:])       # first in the old job order
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["award rows"], 54)
        missing = s.failures("missing:tender_details")
        self.assertEqual(len(missing), 6)
        s.close()

    def test_a_missing_page_is_not_retried_every_night(self):
        ids = self.site.publish(AWARD, 10)
        self.site.home.add(ids[0])
        self.crawl()
        self.crawl()
        self.assertEqual(self.site.count(f"TenderDetails/{ids[0]}"), 0)

    def test_companies_pages_come_before_tender_details(self):
        self.site.publish(AWARD, 30)
        self.crawl(limit_details=30)
        s = self.store()
        self.assertEqual(s.stats()["award rows"], 30)
        s.close()

    def test_awarded_but_no_winner_on_the_page_yet(self):
        ids = self.site.publish(AWARD, 5)
        t = ids[0]
        self.site.no_winner_yet.add(t)
        self.crawl()
        s = self.store()
        self.assertEqual(s.conn.execute(
            "SELECT COUNT(*) FROM company WHERE tender_id=? AND role='awarded'",
            (t,)).fetchone()[0], 0)
        s.close()
        self.site.no_winner_yet.clear()
        self.crawl()
        self.assertEqual(self.site.count(f"TenderCompaniesDetails/{t}"), 1)
        s = self.store()
        self.assertEqual(s.conn.execute(
            "SELECT COUNT(*) FROM company WHERE tender_id=? AND role='awarded'",
            (t,)).fetchone()[0], 1)
        s.close()


class TimeBudgetSplit(Harness):
    """A run with a time budget must not spend all of it on listing pages.

    The first load of Awarded is 1,212 listing pages -- half an hour at the
    polite delay -- and the winners, the bidders and the prices are all on
    the per-tender pages that come after. A 15-minute run that reads nothing
    but listings leaves a database with no company rows in it: nothing to
    match a borrower against, and no way to tell from the numbers that the
    run worked at all.
    """

    def setUp(self):
        super().setUp()
        self.site.publish(AWARD, 2000)                   # 100 listing pages
        self.clock = {"t": 0.0}
        inner = self.site.get

        def ticking(rel):                                # 10 s per request
            self.clock["t"] += 10
            return inner(rel)

        self.site.get = ticking

    def budgeted(self, seconds: float, **kw):
        """A crawl with `seconds` of budget on a clock that only moves when
        the site is asked for a page."""
        self.site.reset()
        args = _args(db=self.db, kind="awarded",
                     max_hours=seconds / 3600, **kw)
        with mock.patch.object(cli.time, "monotonic",
                               lambda: self.clock["t"]):
            cli.cmd_crawl(args)
        listing = sum(1 for r in self.site.requests if "Details/" not in r)
        return listing, len(self.site.requests) - listing

    def test_a_budgeted_run_always_brings_back_some_detail_pages(self):
        listing, detail = self.budgeted(1000)
        self.assertGreater(detail, 0, "a run that reads no detail page has "
                                      "nothing anyone can match against")
        self.assertLess(listing, 100, "listings did not take the whole run")
        s = self.store()
        self.assertGreater(s.stats()["award rows"], 0)
        s.close()

    def test_the_listings_get_the_larger_share(self):
        listing, detail = self.budgeted(1000)
        self.assertGreater(listing, detail,
                           f"{listing} listing vs {detail} detail requests")

    def test_the_next_run_carries_the_first_load_on(self):
        first, _ = self.budgeted(1000)
        second, _ = self.budgeted(1000)
        s = self.store()
        deepest = s.section("awarded")["deepest_page"]
        s.close()
        self.assertGreater(deepest, first, "the first load moved on")

    def test_without_a_budget_nothing_is_cut_short(self):
        self.crawl(listings_only=True)
        s = self.store()
        self.assertTrue(s.section("awarded")["reached_end"])
        s.close()

    def test_a_range_inside_what_is_held_spends_the_run_on_details(self):
        self.budgeted(1000)                              # partial first load
        s = self.store()
        deepest = s.section("awarded")["deepest_page"]
        s.close()
        listing, detail = self.budgeted(1000, pages=f"1-{deepest}")
        self.assertLess(listing, 10, "no listing pages to speak of")
        self.assertGreater(detail, 50)


class RunFrequency(Harness):
    """The rolling pass has to fit how often the tool is run. Nightly runs
    take a slice a night; a run a month later has to cover the section
    itself, or a cancellation deep in the list waits a year."""

    def setUp(self):
        super().setUp()
        from datetime import date, timedelta
        self.site.publish(AWARD, 3000)                     # 150 pages
        today = date.today()
        for i, t in enumerate(self.site.sections[AWARD]):  # newest first
            self.site.dates[t] = (today - timedelta(days=i)).strftime("%d/%m/%Y")
        self.crawl(listings_only=True)
        # an award published now, dated five months back: far down the list
        self.t = self.site.publish("Staging", 1)[0]
        self.site.dates[self.t] = (today - timedelta(days=150)).strftime(
            "%d/%m/%Y")
        self.site.sections[AWARD].insert(1500, self.t)     # page 76

    def _age(self, days: int) -> None:
        """Make the stored runs look `days` older, so the next crawl is one
        that happens that long afterwards."""
        s = self.store()
        s.conn.execute("UPDATE run SET started_at = datetime(started_at, ?)",
                       (f"-{days} days",))
        s.conn.execute("UPDATE section_state SET last_full_pass ="
                       " datetime(last_full_pass, ?)", (f"-{days} days",))
        s.conn.commit()
        s.close()

    def test_a_run_a_month_later_sweeps_the_whole_section(self):
        self._age(30)
        self.crawl(listings_only=True, roll_pages=None)
        s = self.store()
        self.assertEqual(s.current_state(self.t), "awarded")
        self.assertIsNone(s.section("awarded")["roll_next"],
                          "a monthly run has to finish the pass itself")
        s.close()

    def test_nightly_runs_take_slices_and_still_get_there(self):
        most = 0
        for night in range(1, 15):
            self._age(1)
            self.crawl(listings_only=True, roll_pages=None)
            most = max(most, self.site.count("AwardedTenders/"))
            if self.store_state() == "awarded":
                break
        self.assertEqual(self.store_state(), "awarded",
                         "found within a fortnight of nightly runs")
        self.assertLessEqual(most, 110, "no single night reads the section")

    def test_an_explicit_cap_still_wins(self):
        self._age(30)
        self.crawl(listings_only=True, roll_pages=5)
        self.assertLess(self.site.count("AwardedTenders/"), 20)

    def store_state(self):
        s = self.store()
        try:
            return s.current_state(self.t)
        finally:
            s.close()


class GoneMarking(Harness):
    def test_reappearing_clears_the_mark(self):
        ids = self.site.publish(TECH, 5)
        self.crawl(kind="technical")
        t = ids[0]
        self.site.sections[TECH].remove(t)
        self.crawl(kind="technical")
        self.crawl(kind="technical")
        s = self.store()
        self.assertIsNotNone(s.conn.execute(
            "SELECT gone_at FROM sighting WHERE tender_id=?", (t,)).fetchone()[0])
        s.close()
        self.site.sections[TECH].insert(0, t)
        self.crawl(kind="technical")
        s = self.store()
        row = s.conn.execute("SELECT gone_at, missed FROM sighting"
                             " WHERE tender_id=?", (t,)).fetchone()
        self.assertEqual((row["gone_at"], row["missed"]), (None, 0))
        s.close()

    def test_a_failed_page_means_no_gone_marking(self):
        self.site.publish(TECH, 3 * PER_PAGE)
        self.crawl(kind="technical")
        self.site.hang.add(f"/TendersOnlineServices/{TECH}/2")
        self.crawl(kind="technical")
        self.crawl(kind="technical")
        s = self.store()
        self.assertEqual(s.conn.execute(
            "SELECT COUNT(*) FROM sighting WHERE gone_at IS NOT NULL"
        ).fetchone()[0], 0)
        s.close()


if __name__ == "__main__":
    unittest.main()
