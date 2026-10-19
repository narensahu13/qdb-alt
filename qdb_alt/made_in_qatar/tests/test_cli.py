"""The commands end to end against a stand-in site: load, a second load that
fetches only what changed, a failed load, and matching a customer book."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from made_in_qatar.cli import main
    from made_in_qatar.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from made_in_qatar.cli import main
    from made_in_qatar.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_site import BLUE, SITEMAP, SUNRISE, FakeSite  # noqa: E402


def run(site, *argv) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(list(argv), session=site)
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.site = FakeSite()

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return run(self.site, "--db", self.db, "load", "--delay", "0")

    def test_first_load_reads_every_page_and_a_second_only_what_changed(self):
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("2 factories, 2 pages fetched", printed)
        self.site.sitemap = self.site.sitemap.replace("2025-01-10T10:00:00", "2026-10-01T10:00:00")
        code, printed = self.load()
        self.assertIn("2 factories, 1 pages fetched", printed)
        self.assertEqual((self.site.calls[SUNRISE], self.site.calls[BLUE]), (1, 2))
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs()], ["ok", "ok"])

    def test_a_factory_dropped_from_the_sitemap_is_marked_not_listed(self):
        self.load()
        self.site.sitemap = self.site.sitemap.replace(BLUE, BLUE + "gone/")
        self.site.pages[BLUE + "gone/"] = self.site.pages[BLUE]
        self.load()
        with Store(self.db) as store:
            names = [f["url"] for f in store.factories()]
        self.assertNotIn(BLUE, names)

    def test_a_failed_page_fails_the_run_and_is_logged(self):
        self.site.failing.add(BLUE)
        with self.assertRaises(Exception):
            self.load()
        with Store(self.db) as store:
            (last,) = store.runs(1)
        self.assertEqual(last["status"], "error")
        self.assertIn("503", last["error"])

    def test_match_writes_features_and_leaves_unknowns_empty(self):
        self.load()
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,SUNRISE DATES FACTORY W.L.L,000000012345\n"
                        "C2,SUNSET DATES FACTORY,000000054321\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(self.site, "--db", self.db, "match", str(book), "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("1 of 2 customers found", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        self.assertEqual((rows["C1"]["miq_listed"], rows["C1"]["miq_founded_year"], rows["C1"]["miq_products"]),
                         ("1", "2015", "2"))
        self.assertEqual(rows["C1"]["miq_sector"], "Manufacture of food products")
        self.assertEqual((rows["C2"]["miq_listed"], rows["C2"]["miq_founded_year"]), ("0", ""))
        self.assertEqual(self.site.calls[SITEMAP], 1)      # match works offline from the database


if __name__ == "__main__":
    unittest.main()
