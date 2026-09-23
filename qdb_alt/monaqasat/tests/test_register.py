"""The classified register: refresh passes and removed companies."""

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from monaqasat import cli
from monaqasat.fetch import Fetcher
from monaqasat.store import Store

try:
    from test_incremental import FakeRegister
except ImportError:                                   # run as tests.test_...
    from tests.test_incremental import FakeRegister


class RegisterPasses(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "r.db")
        self.reg = FakeRegister(200)                     # 10 pages
        reg = self.reg
        for p in (mock.patch.object(Fetcher, "check_robots", lambda s: ""),
                  mock.patch.object(Fetcher, "get", lambda s, rel: reg.get(rel)),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, **kw):
        self.reg.requests.clear()
        a = dict(db=self.db, pages=None, full=False, delay=0, timeout=5,
                 register_pages=20, register_days=30)
        a.update(kw)
        return cli.cmd_companies(Namespace(**a))

    def delisted(self):
        s = Store(self.db)
        rows = [r[0] for r in s.conn.execute(
            "SELECT profile_number FROM classified_company"
            " WHERE delisted_at IS NOT NULL ORDER BY 1")]
        s.close()
        return rows

    def test_a_removed_company_is_delisted_after_two_full_passes(self):
        self.run_()                                      # pass 1: first load
        gone = self.reg.pids[45]
        self.reg.pids.remove(gone)
        self.run_(full=True)                             # pass 2: one miss
        self.assertEqual(self.delisted(), [])
        self.run_(full=True)                             # pass 3: two misses
        self.assertEqual(self.delisted(), [gone])

    def test_a_company_that_comes_back_is_listed_again(self):
        self.run_()
        gone = self.reg.pids.pop(45)
        self.run_(full=True)
        self.run_(full=True)
        self.reg.pids.insert(45, gone)
        self.run_(full=True)
        self.assertEqual(self.delisted(), [])

    def test_an_update_refreshes_a_slice_once_a_pass_is_due(self):
        self.run_()
        self.run_(register_days=0, register_pages=3)
        # page 1, the tail (pages 9-10), and pages 1-3 of the new pass
        self.assertLessEqual(len(self.reg.requests), 6)
        s = Store(self.db)
        self.assertEqual(s.section("classified")["roll_next"], 4)
        s.close()

    def test_a_pass_spread_over_runs_completes(self):
        self.run_()
        for _ in range(4):
            self.run_(register_days=0, register_pages=3)
        s = Store(self.db)
        sec = s.section("classified")
        s.close()
        # first pass (the load), then a second one of 10 pages in slices
        self.assertEqual(sec["pass_id"], 2)
        self.assertIsNone(sec["roll_next"])

    def test_a_failed_page_holds_the_pass_open(self):
        """Completing a pass with a page unread would count every company
        on it as missing."""
        self.run_()
        self.reg.hang.add("/ClassifiedCompaniesProfilesList/4")
        self.run_(full=True)
        s = Store(self.db)
        self.assertIsNotNone(s.section("classified")["roll_next"])
        self.assertEqual(s.conn.execute(
            "SELECT MAX(missed) FROM classified_company").fetchone()[0], 0)
        s.close()
        self.reg.hang.clear()
        self.run_()                                      # retries page 4
        s = Store(self.db)
        self.assertIsNone(s.section("classified")["roll_next"])
        s.close()

    def test_reparse_does_not_reset_delisting(self):
        self.run_()
        gone = self.reg.pids.pop(45)
        self.run_(full=True)
        self.run_(full=True)
        self.assertEqual(self.delisted(), [gone])
        # an old stored page that still lists it is re-parsed
        s = Store(self.db)
        s.upsert_company({"profile_number": gone, "name": f"COMPANY {gone}",
                          "cr_number": "1", "activities": []}, page=3)
        s.commit()
        s.close()
        cli.cmd_parse(Namespace(db=self.db))
        self.assertEqual(self.delisted(), [gone])

    def test_seen_again_on_the_live_register_clears_it(self):
        self.run_()
        gone = self.reg.pids.pop(45)
        self.run_(full=True)
        self.run_(full=True)
        s = Store(self.db)
        s.upsert_company({"profile_number": gone, "name": "X",
                          "cr_number": "1", "activities": []},
                         page=3, pass_id=9)
        s.commit()
        s.close()
        self.assertEqual(self.delisted(), [])


class RegisterFrequency(unittest.TestCase):
    """The refresh pass has to fit how often `companies` is run: a slice a
    night, or the whole register when the run comes a month later."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "r.db")
        self.reg = FakeRegister(1000)                    # 50 pages
        reg = self.reg
        for p in (mock.patch.object(Fetcher, "check_robots", lambda s: ""),
                  mock.patch.object(Fetcher, "get", lambda s, rel: reg.get(rel)),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)
        self.run_()                                      # first load

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, **kw):
        self.reg.requests.clear()
        a = dict(db=self.db, pages=None, full=False, delay=0, timeout=5,
                 register_pages=None, register_days=30)
        a.update(kw)
        return cli.cmd_companies(Namespace(**a))

    def _age(self, days: int) -> None:
        s = Store(self.db)
        s.conn.execute("UPDATE run SET started_at = datetime(started_at, ?)",
                       (f"-{days} days",))
        s.conn.execute("UPDATE section_state SET last_full_pass ="
                       " datetime(last_full_pass, ?)", (f"-{days} days",))
        s.conn.commit()
        s.close()

    def test_a_run_a_month_later_refreshes_every_page(self):
        self._age(30)
        self.run_()
        pages = {int(r.rsplit("/", 1)[1]) for r in self.reg.requests}
        self.assertGreaterEqual(len(pages), 50)
        s = Store(self.db)
        sec = s.section("classified")
        s.close()
        self.assertIsNone(sec["roll_next"], "the pass finished in this run")

    def test_a_run_the_next_day_only_reads_the_tail(self):
        self._age(1)
        self.run_()
        self.assertLessEqual(len(self.reg.requests), 5)

    def test_an_explicit_cap_still_wins(self):
        self._age(30)
        self.run_(register_pages=5)
        self.assertLessEqual(len(self.reg.requests), 10)


if __name__ == "__main__":
    unittest.main()
