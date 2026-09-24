"""The crawl, end to end, against a stand-in portal.

The question every test here asks is the one that decides whether this is
usable next year: *run it again next month -- does it do only the work that
is left?*
"""

from __future__ import annotations

import io
import sys
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from icv import cli
    from icv.store import Store
except ImportError:                                   # run from the package dir
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv import cli
    from icv.store import Store

try:
    from fake_portal import Company, FakePortal, sample
except ImportError:
    from tests.fake_portal import Company, FakePortal, sample


def args(**kw):
    base = dict(db=None, delay=0.0, timeout=5, no_system_trust=True,
                session=None, page_size=2, limit=None, max_hours=None,
                listings_only=False, changed_only=False, goods=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def run(fn, a) -> str:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        fn(a)
    return out.getvalue()


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "icv.db")
        self.companies = sample(5)
        self.portal = FakePortal(self.companies)
        self.addCleanup(self.tmp.cleanup)

    def crawl(self, **kw):
        self.portal.requests.clear()
        return run(cli.cmd_crawl,
                   args(db=self.db, session=self.portal, **kw))

    def store(self) -> Store:
        return Store(self.db)

    def details_fetched(self) -> int:
        return sum(1 for r in self.portal.requests
                   if r.startswith("GET") and "supplier-score-history" in r)


class FirstLoad(Harness):
    def test_every_company_and_its_whole_history_is_stored(self):
        self.crawl()
        s = self.store()
        st = s.stats()
        self.assertEqual(st["companies"], 5)
        self.assertEqual(st["history read"], 5)
        self.assertEqual(st["score observations"], 10)   # two each
        s.close()

    def test_a_companys_history_needs_its_own_page(self):
        # The signed configuration carries the identity; one page cannot be
        # reused for another company. Five companies, five page fetches.
        self.crawl()
        self.assertEqual(self.details_fetched(), 5)

    def test_the_sweep_stops_at_the_end_despite_a_lying_item_count(self):
        # ItemCount is capped and asking past the end serves the last page
        # again, so the pager has to stop on MoreRecords and on a page that
        # brings nothing new.
        out = self.crawl()
        self.assertIn("(last page)", out)
        s = self.store()
        self.assertEqual(s.stats()["companies"], 5)
        s.close()

    def test_the_listing_state_opens_for_everyone(self):
        self.crawl()
        s = self.store()
        rows = s.conn.execute(
            "SELECT COUNT(*) FROM listing_state WHERE to_date IS NULL"
        ).fetchone()[0]
        self.assertEqual(rows, 5)
        s.close()


class SecondRun(Harness):
    def test_nothing_moved_means_no_detail_page_is_read_again(self):
        self.crawl()
        self.crawl()
        self.assertEqual(self.details_fetched(), 0)

    def test_only_the_company_that_moved_is_re_read(self):
        self.crawl()
        moved = self.companies[2]
        moved.post("2026-02-01", moved.score + 3.5, "Certificate Renewed", 3.5)
        moved.certificate = "2000000"
        self.crawl()
        self.assertEqual(self.details_fetched(), 1)
        s = self.store()
        self.assertEqual(s.stats()["score observations"], 11)
        s.close()

    def test_an_existing_observation_is_never_rewritten(self):
        self.crawl()
        s = self.store()
        before = s.conn.execute(
            "SELECT id, first_seen, score FROM score_observation"
            " ORDER BY id").fetchall()
        s.close()
        moved = self.companies[0]
        moved.post("2026-03-01", 21.0, "Certificate Renewed", 4.0)
        moved.certificate = "2000001"
        self.crawl()
        s = self.store()
        after = {r["id"]: r for r in s.conn.execute(
            "SELECT id, first_seen, score FROM score_observation")}
        for row in before:
            self.assertEqual(after[row["id"]]["first_seen"], row["first_seen"])
            self.assertEqual(after[row["id"]]["score"], row["score"])
        s.close()

    def test_a_status_change_is_picked_up_and_re_reads_that_company(self):
        # How the period is closed is test_store's business; what matters
        # here is that the sweep noticed and went back for the history.
        self.crawl()
        c = self.companies[1]
        c.status, c.status_code = "Grace Period", 100000005
        out = self.crawl()
        self.assertIn("1 changed", out)
        self.assertEqual(self.details_fetched(), 1)
        s = self.store()
        now = s.status_as_at(c.guid)
        self.assertEqual(now["status"], "Grace Period")
        s.close()


class Disappearing(Harness):
    def test_one_absence_is_not_enough_to_call_a_company_gone(self):
        self.crawl()
        dropped = self.companies[3]
        self.portal.order.remove(dropped.guid)
        self.crawl()
        s = self.store()
        row = s.conn.execute("SELECT gone_at, misses FROM company WHERE id = ?",
                             (dropped.guid,)).fetchone()
        self.assertIsNone(row["gone_at"])
        self.assertEqual(row["misses"], 1)
        s.close()

    def test_two_absences_close_it_but_keep_its_rows(self):
        self.crawl()
        dropped = self.companies[3]
        self.portal.order.remove(dropped.guid)
        self.crawl()
        self.crawl()
        s = self.store()
        row = s.conn.execute("SELECT gone_at FROM company WHERE id = ?",
                             (dropped.guid,)).fetchone()
        self.assertIsNotNone(row["gone_at"])
        kept = s.conn.execute(
            "SELECT COUNT(*) FROM score_observation WHERE company_id = ?",
            (dropped.guid,)).fetchone()[0]
        self.assertEqual(kept, 2, "history is evidence; it is never deleted")
        closed = s.conn.execute(
            "SELECT to_date FROM listing_state WHERE company_id = ?"
            " ORDER BY from_date DESC LIMIT 1", (dropped.guid,)).fetchone()
        self.assertIsNotNone(closed["to_date"])
        s.close()

    def test_an_interrupted_sweep_marks_nothing_as_gone(self):
        self.crawl()
        self.portal.fail_after = 2            # die partway through the list
        out = self.crawl()
        self.portal.fail_after = None
        self.assertIn("did not finish", out)
        s = self.store()
        gone = s.conn.execute(
            "SELECT COUNT(*) FROM company WHERE gone_at IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(gone, 0)
        s.close()


class Budget(Harness):
    def test_limit_leaves_the_rest_for_the_next_run(self):
        self.crawl(limit=2)
        s = self.store()
        self.assertEqual(s.stats()["history read"], 2)
        self.assertEqual(len(s.needs_history([])), 3)
        s.close()
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["history read"], 5)
        self.assertEqual(len(s.needs_history([])), 0)
        s.close()

    def test_listings_only_reads_no_detail_page(self):
        self.crawl(listings_only=True)
        self.assertEqual(self.details_fetched(), 0)
        s = self.store()
        self.assertEqual(s.stats()["companies"], 5)
        self.assertEqual(s.stats()["score observations"], 0)
        s.close()

    def test_changed_only_ignores_the_backlog(self):
        self.crawl(listings_only=True)
        self.crawl(changed_only=True)
        self.assertEqual(self.details_fetched(), 0)

    def test_a_run_that_read_no_details_says_what_is_waiting(self):
        out = self.crawl(listings_only=True)
        out = self.crawl(limit=0)
        self.assertIn("still have no score history", out)


class GoodsAndServices(Harness):
    def test_they_are_off_unless_asked_for(self):
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["services"], 0)
        s.close()

    def test_and_are_read_when_asked_for(self):
        self.crawl(goods=True)
        s = self.store()
        self.assertGreater(s.stats()["services"], 0)
        s.close()


if __name__ == "__main__":
    unittest.main()
