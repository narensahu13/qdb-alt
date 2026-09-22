"""The reporting commands, end to end on a simulated crawl."""

import contextlib
import csv
import io
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from monaqasat import cli
from monaqasat.fetch import Fetcher

try:
    from test_incremental import FakeSite, _args
except ImportError:                                   # run as tests.test_...
    from tests.test_incremental import FakeSite, _args


class Reports(unittest.TestCase):
    """Tender W won by the customer, L lost by it, P still open."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "t.db")
        site = FakeSite()
        w, l, p = site.publish("TechnicallyOpenedTenders", 3)
        cls.ids = (w, l, p)
        with mock.patch.object(Fetcher, "check_robots", lambda self: ""), \
                mock.patch.object(Fetcher, "get",
                                  lambda self, rel: site.get(rel)), \
                mock.patch("builtins.print"):
            cli.cmd_crawl(_args(db=cls.db, kind="technical,awarded"))
            for t in (w, l):
                site.sections["TechnicallyOpenedTenders"].remove(t)
                site.sections.setdefault("AwardedTenders", []).insert(0, t)
            cli.cmd_crawl(_args(db=cls.db, kind="technical,awarded"))
        # The fake page names "CO <id>" (CR <id>) as winner and "OTHER <id>"
        # (CR 9<id>) as the other bidder. Our customer is CO w, and OTHER l.
        cls.customers = Path(cls.tmp.name) / "c.csv"
        cls.customers.write_text(
            "customer_id,name,cr_number\n"
            f"CUST,Anything,{w}\n"
            f"CUST2,Anything else,9{l}\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cmd(self, func, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = func(Namespace(db=self.db, **kw))
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_match_profile_analyse(self):
        out = self.run_cmd(cli.cmd_match, customers=str(self.customers),
                           sheet=None, fuzzy=0.92, translit=0.9,
                           replace=False, out=None)
        self.assertIn("cr ", out)
        prof = self.run_cmd(cli.cmd_profile, customer="CUST2")
        self.assertIn("won / lost / open: 0 / 1 / 0", prof)
        ana = self.run_cmd(cli.cmd_analyse, min_bids=1, limit=10, out=None)
        self.assertIn("OTHER", ana)

    def test_rematching_replaces_that_customers_rows(self):
        for _ in range(2):
            self.run_cmd(cli.cmd_match, customers=str(self.customers),
                         sheet=None, fuzzy=0.92, translit=0.9, replace=False,
                         out=None)
        stats = self.run_cmd(cli.cmd_stats)
        self.assertIn("matches", stats)

    def test_history_shows_the_lifecycle(self):
        w = self.ids[0]
        out = self.run_cmd(cli.cmd_history, tender=w)
        self.assertIn("technical", out)
        self.assertIn("awarded", out)
        self.assertIn("state: awarded", out)

    def test_status_and_export(self):
        self.assertIn("technical", self.run_cmd(cli.cmd_status, runs=3))
        path = Path(self.tmp.name) / "s.csv"
        self.run_cmd(cli.cmd_export, table="sightings", out=str(path))
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue({"tender_id", "kind", "gone_at"} <= set(rows[0]))

    def test_features_build(self):
        out = self.run_cmd(cli.cmd_features, months=3, as_of_end=None)
        self.assertIn("rows", out)


if __name__ == "__main__":
    unittest.main()
