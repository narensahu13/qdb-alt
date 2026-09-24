"""The commands, including the one that runs while a loan is being decided.

`refresh` is the live fallback: an applicant with no row in the mirror. It has
to write the same rows a sweep would, so that the answer next month comes out
of the database and not off the internet.
"""

from __future__ import annotations

import csv
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
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv import cli
    from icv.store import Store

try:
    from fake_portal import FakePortal, sample
except ImportError:
    from tests.fake_portal import FakePortal, sample


def run(fn, **kw) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        code = fn(types.SimpleNamespace(**kw))
    return code, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = str(self.dir / "icv.db")
        self.companies = sample(4)
        self.portal = FakePortal(self.companies)

    def crawl(self, **kw):
        opts = dict(db=self.db, delay=0.0, timeout=5, no_system_trust=True,
                    session=self.portal, page_size=2, limit=None,
                    max_hours=None, listings_only=False, changed_only=False,
                    goods=False)
        opts.update(kw)
        return run(cli.cmd_crawl, **opts)

    def book(self, rows) -> str:
        path = self.dir / "book.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["customer_id", "name", "cr_number"])
            w.writerows(rows)
        return str(path)


class Refresh(Base):
    def test_an_applicant_not_in_the_mirror_is_fetched_and_stored(self):
        wanted = self.companies[2]
        code, out = run(cli.cmd_refresh, db=self.db, delay=0.0, timeout=5,
                        no_system_trust=True, session=self.portal,
                        cr=wanted.cr, name=None, limit=1, show=0)
        self.assertEqual(code, 0)
        self.assertIn(wanted.name, out)
        s = Store(self.db)
        try:
            self.assertEqual(s.stats()["companies"], 1)
            self.assertEqual(s.stats()["score observations"],
                             len(wanted.history))
            # and it is queryable as at a date, like anything else
            self.assertIsNotNone(s.score_as_at(wanted.guid, "2030-01-01"))
        finally:
            s.close()

    def test_a_company_that_is_not_certified_says_so(self):
        code, out = run(cli.cmd_refresh, db=self.db, delay=0.0, timeout=5,
                        no_system_trust=True, session=self.portal,
                        cr="999999", name=None, limit=1, show=0)
        self.assertEqual(code, 1)
        self.assertIn("not a certified supplier", out)

    def test_it_writes_the_same_rows_a_sweep_would(self):
        wanted = self.companies[0]
        run(cli.cmd_refresh, db=self.db, delay=0.0, timeout=5,
            no_system_trust=True, session=self.portal, cr=wanted.cr,
            name=None, limit=1, show=0)
        self.crawl()
        s = Store(self.db)
        try:
            # the sweep found nothing new for that company to add
            n = s.conn.execute(
                "SELECT COUNT(*) FROM score_observation WHERE company_id = ?",
                (wanted.guid,)).fetchone()[0]
            self.assertEqual(n, len(wanted.history))
        finally:
            s.close()


class MatchCommand(Base):
    def test_the_csv_carries_the_score_beside_the_customer(self):
        self.crawl()
        target = self.companies[1]
        book = self.book([["CIF-1", "whatever", target.cr]])
        out_csv = str(self.dir / "matches.csv")
        code, out = run(cli.cmd_match, db=self.db, customers=book, sheet=None,
                        out=out_csv, replace=False, fuzzy=0.92, translit=0.90)
        self.assertEqual(code, 0)
        with open(out_csv, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["customer_id"], "CIF-1")
        self.assertEqual(rows[0]["method"], "cr")
        self.assertEqual(rows[0]["matched_name"], target.name)
        self.assertTrue(rows[0]["icv_score"])
        self.assertEqual(rows[0]["status"], "Valid")

    def test_matching_before_crawling_says_what_to_do(self):
        book = self.book([["CIF-1", "x", "100000"]])
        code, out = run(cli.cmd_match, db=self.db, customers=book, sheet=None,
                        out=None, replace=False, fuzzy=0.92, translit=0.90)
        self.assertEqual(code, 1)
        self.assertIn("Run `crawl` first", out)

    def test_matches_are_stored_as_well_as_written(self):
        self.crawl()
        book = self.book([["CIF-1", "x", self.companies[1].cr]])
        run(cli.cmd_match, db=self.db, customers=book, sheet=None, out=None,
            replace=False, fuzzy=0.92, translit=0.90)
        s = Store(self.db)
        try:
            self.assertEqual(s.stats()["matches"], 1)
        finally:
            s.close()


class Reporting(Base):
    def test_status_and_stats_before_anything_is_crawled(self):
        code, out = run(cli.cmd_status, db=self.db)
        self.assertEqual(code, 1)
        self.assertIn("Run `crawl` first", out)

    def test_status_after_a_run(self):
        self.crawl()
        code, out = run(cli.cmd_status, db=self.db)
        self.assertEqual(code, 0)
        self.assertIn("last run", out)

    def test_stats_groups_the_reasons_a_score_moved(self):
        self.crawl()
        code, out = run(cli.cmd_stats, db=self.db)
        self.assertEqual(code, 0)
        self.assertIn("why the score moved", out)
        self.assertIn("Transitional Percentage Added", out)

    def test_export_writes_a_table(self):
        self.crawl()
        path = str(self.dir / "scores.csv")
        code, out = run(cli.cmd_export, db=self.db, table="scores", out=path)
        self.assertEqual(code, 0)
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 8)
        self.assertIn("posted_date", rows[0])

    def test_exporting_an_empty_table_says_so(self):
        self.crawl(listings_only=True)
        code, out = run(cli.cmd_export, db=self.db, table="scores",
                        out=str(self.dir / "x.csv"))
        self.assertEqual(code, 1)
        self.assertIn("Nothing in scores", out)


if __name__ == "__main__":
    unittest.main()
