"""Reading the two programme pages, and the commands end to end against a
stand-in site. The fixture pages are made up, in the real markup: zero-width
spaces in cells, a certificate number broken by spaces, and an accreditation
year that spans two rows."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from customs_trusted_traders import fetch
    from customs_trusted_traders.cli import main
    from customs_trusted_traders.parse import aeo, green_list
    from customs_trusted_traders.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from customs_trusted_traders import fetch
    from customs_trusted_traders.cli import main
    from customs_trusted_traders.parse import aeo, green_list
    from customs_trusted_traders.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
AEO_PAGE = (FIXTURES / "aeo_page.html").read_text(encoding="utf-8")
GREEN_PAGE = (FIXTURES / "green_list_page.html").read_text(encoding="utf-8")


class Response:
    def __init__(self, status, text):
        self.status_code, self.text, self.encoding = status, text, "utf-8"


class FakeSite:
    def __init__(self):
        self.pages = {fetch.PAGES["aeo"]: AEO_PAGE, fetch.PAGES["green_list"]: GREEN_PAGE}

    def get(self, url, headers=None, timeout=None):
        return Response(200, self.pages[url]) if url in self.pages else Response(404, "")


class Pages(unittest.TestCase):
    def test_aeo_rows_with_spanning_year_and_cleaned_certificate_numbers(self):
        self.assertEqual(aeo(AEO_PAGE), [
            {"programme": "aeo", "year": 2019, "certificate": "QA-AEO-000001", "name": "SUNRISE DATES FACTORY W.L.L"},
            {"programme": "aeo", "year": 2019, "certificate": "QA-AEO-000002", "name": "BLUE HARBOUR ICE"},
            {"programme": "aeo", "year": 2026, "certificate": "QA-AEO-010015", "name": "NORTHWIND PLASTIC INDUSTRIES"},
        ])

    def test_green_list_skips_the_header(self):
        self.assertEqual([r["name"] for r in green_list(GREEN_PAGE)], ["Sunrise Dates Factory", "Golden Harbour Trading"])


def run(site, *argv):
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

    def test_load_and_match(self):
        self.assertEqual(run(self.site, "--db", self.db, "load", "--delay", "0")[0], 0)
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,SUNRISE DATES FACTORY,000000012345\n"
                        "C2,NORTHWIND PLASTIC INDUSTRIES W.L.L,000000022222\n"
                        "C3,GOLDEN HARBOUR ICE,000000011111\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(self.site, "--db", self.db, "match", str(book), "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("2 of 3 customers on a list", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        self.assertEqual((rows["C1"]["customs_aeo"], rows["C1"]["customs_aeo_year"], rows["C1"]["customs_green_list"]),
                         ("1", "2019", "1"))
        self.assertEqual((rows["C2"]["customs_aeo"], rows["C2"]["customs_aeo_year"]), ("1", "2026"))
        # shares HARBOUR ICE with one list entry and GOLDEN HARBOUR with another, but neither is this company
        self.assertEqual((rows["C3"]["customs_aeo"], rows["C3"]["customs_green_list"], rows["C3"]["customs_aeo_year"]),
                         ("0", "0", ""))

    def test_a_company_dropped_from_a_list_is_kept_as_not_listed(self):
        run(self.site, "--db", self.db, "load", "--delay", "0")
        self.site.pages[fetch.PAGES["green_list"]] = GREEN_PAGE.replace("Golden Harbour Trading", "Westbay Ice")
        code, printed = run(self.site, "--db", self.db, "load", "--delay", "0")
        self.assertIn("green_list 2 (1 dropped)", printed)
        with Store(self.db) as store:
            self.assertEqual([t["name"] for t in store.traders("green_list")], ["Sunrise Dates Factory", "Westbay Ice"])

    def test_an_error_page_fails_the_run_and_changes_nothing(self):
        run(self.site, "--db", self.db, "load", "--delay", "0")
        self.site.pages[fetch.PAGES["green_list"]] = "<html>Sorry, something went wrong</html>"
        with self.assertRaises(fetch.SiteError):
            run(self.site, "--db", self.db, "load", "--delay", "0")
        with Store(self.db) as store:
            self.assertEqual(store.runs(1)[0]["status"], "error")
            self.assertEqual(len(store.traders("green_list")), 2)


if __name__ == "__main__":
    unittest.main()
