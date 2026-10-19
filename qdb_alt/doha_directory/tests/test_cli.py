"""The commands end to end against a stand-in site: load, a second load that
fetches only what changed, a page the site removed, a page that fails, and
matching a customer book."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from doha_directory.cli import main
    from doha_directory.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from doha_directory.cli import main
    from doha_directory.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_site import BLUE, PEARL, SITEMAP, SUNRISE, FakeSite  # noqa: E402

BOOK = ("customer_id,name,cr_number,search_name\n"
        "C1,SUNRISE DATES FACTORY W.L.L,000000012345,\n"
        "C2,SUNSET DATES FACTORY,000000054321,\n"
        "C3,PEARL COAST LAW FIRM W.L.L,000000011111,\n"
        "C4,AL BAHAR TRADING AND CONTRACTING,000000022222,Blue Harbour Ice\n")


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

    def match(self) -> dict[str, dict[str, str]]:
        book = Path(self.tmp.name) / "book.csv"
        book.write_text(BOOK, encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, self.printed = run(self.site, "--db", self.db, "match", str(book), "--out", str(out))
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8-sig") as f:
            return {r["customer_id"]: r for r in csv.DictReader(f)}

    def test_first_load_reads_every_page_and_a_second_only_what_changed(self):
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("4 listings, 4 pages fetched, 0 failed", printed)
        self.site.sitemap = self.site.sitemap.replace("2025-01-10T10:00:00", "2026-10-01T10:00:00")
        code, printed = self.load()
        self.assertIn("4 listings, 1 pages fetched", printed)
        self.assertEqual((self.site.calls[SUNRISE], self.site.calls[BLUE]), (1, 2))
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs()], ["ok", "ok"])

    def test_a_listing_dropped_from_the_sitemap_is_marked_not_listed(self):
        self.load()
        self.site.sitemap = self.site.sitemap.replace(BLUE, BLUE.replace("2202", "2299"))
        self.site.pages[BLUE.replace("2202", "2299")] = self.site.pages[BLUE]
        self.load()
        with Store(self.db) as store:
            urls = [li["url"] for li in store.listings()]
        self.assertNotIn(BLUE, urls)
        self.assertEqual(len(urls), 4)

    def test_a_page_the_site_removed_is_not_listed_and_the_run_is_ok(self):
        del self.site.pages[BLUE]
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("3 listings, 3 pages fetched, 0 failed, 1 removed from the site", printed)

    def test_a_failed_page_fails_the_run_keeps_the_rest_and_leaves_its_customers_unknown(self):
        self.site.failing.add(PEARL)
        with self.assertRaises(Exception):
            self.load()
        with Store(self.db) as store:
            (last,) = store.runs(1)
            listings = {li["url"]: li for li in store.listings()}
        self.assertEqual(last["status"], "error")
        self.assertIn("503", last["error"])
        self.assertIn("1 failed", last["note"])
        self.assertEqual(self.site.calls[PEARL], 2)                      # tried once more
        self.assertEqual((listings[PEARL]["page_read"], listings[SUNRISE]["page_read"]), (0, 1))

        rows = self.match()
        self.assertEqual({k: rows["C3"][k] for k in ("dd_listed", "dd_listings", "dd_category")},
                         {"dd_listed": "", "dd_listings": "", "dd_category": ""})   # unknown, not 0
        self.assertEqual(rows["C1"]["dd_listed"], "1")
        self.assertIn("1 unknown", self.printed)

        self.site.failing.clear()                                        # the next load fetches only that page
        code, printed = self.load()
        self.assertIn("4 listings, 1 pages fetched, 0 failed", printed)
        self.assertEqual(self.match()["C3"]["dd_listed"], "1")

    def test_match_writes_features_and_leaves_unknowns_empty(self):
        self.load()
        rows = self.match()
        self.assertIn("3 of 4 customers found, 0 unknown", self.printed)
        c1 = rows["C1"]
        self.assertEqual({k: c1[k] for k in ("dd_listed", "dd_listings", "dd_category", "dd_rating", "dd_reviews",
                                             "dd_has_phone", "dd_has_email", "dd_social_links", "dd_open_days",
                                             "dd_founded_year", "dd_last_updated")},
                         {"dd_listed": "1", "dd_listings": "2", "dd_category": "Food Manufacturing",
                          "dd_rating": "4.4", "dd_reviews": "37", "dd_has_phone": "1", "dd_has_email": "1",
                          "dd_social_links": "2", "dd_open_days": "6", "dd_founded_year": "2015",
                          "dd_last_updated": "2024-09-03"})
        self.assertEqual((c1["url"], c1["matched_name"]), (SUNRISE, "Sunrise Dates Factory"))   # the reviewed one
        self.assertEqual((rows["C2"]["dd_listed"], rows["C2"]["dd_listings"], rows["C2"]["dd_rating"]), ("0", "0", ""))
        self.assertEqual((rows["C3"]["dd_listed"], rows["C3"]["dd_category"]), ("1", "Law Firms"))  # by its address
        c4 = rows["C4"]                                                   # found under its search_name
        self.assertEqual((c4["dd_listed"], c4["dd_reviews"], c4["dd_rating"], c4["dd_social_links"],
                          c4["dd_has_phone"], c4["dd_open_days"]), ("1", "0", "", "0", "0", ""))
        self.assertEqual(self.site.calls[SITEMAP], 1)      # match works offline from the database

    def test_no_personal_contact_details_are_stored(self):
        self.load()
        with Store(self.db) as store:
            cols = [r[1] for r in store.db.execute("PRAGMA table_info(listing)")]
            values = " ".join(str(v) for li in store.listings() for v in li.values())
        self.assertNotIn("telephone", cols)
        self.assertNotIn("info@sunrisedates.example", values)
        self.assertNotIn("40000000", values)


if __name__ == "__main__":
    unittest.main()
