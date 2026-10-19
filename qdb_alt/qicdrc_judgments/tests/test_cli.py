"""The commands end to end against a stand-in site: load, a second load that
fetches only what changed, a load stopped part-way and continued, failed
pages (a few are retried next time, many fail the run), robots.txt, and
matching a customer book."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

try:
    from qicdrc_judgments.cli import main
    from qicdrc_judgments.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qicdrc_judgments.cli import main
    from qicdrc_judgments.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_site import APPEAL, FIXTURES, ROBOTS, SITEMAP, SUNRISE, FakeSite  # noqa: E402


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

    def load(self, *extra):
        return run(self.site, "--db", self.db, "load", "--delay", "0", *extra)

    def last_run(self):
        with Store(self.db) as store:
            return store.runs(1)[0]

    def test_first_load_reads_every_page_and_a_second_only_what_changed(self):
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("2 judgments in the sitemap, 2 pages fetched", printed)
        self.site.lastmod[APPEAL] = "2026-10-01T10:00:00+03:00"
        code, printed = self.load()
        self.assertIn("2 judgments in the sitemap, 1 pages fetched", printed)
        self.assertEqual((self.site.calls[SUNRISE], self.site.calls[APPEAL]), (1, 2))
        self.assertFalse(any("/ar/" in u for u in self.site.calls))       # English pages only
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs()], ["ok", "ok"])
            self.assertEqual(len(store.parties()), 5)

    def test_a_judgment_dropped_from_the_sitemap_is_marked_not_listed(self):
        self.load()
        del self.site.lastmod[APPEAL]
        code, printed = self.load()
        self.assertIn("1 no longer listed", printed)
        with Store(self.db) as store:
            self.assertEqual([j["url"] for j in store.judgments()], [SUNRISE])

    def test_a_load_stopped_at_max_pages_continues_where_it_stopped(self):
        self.site.add_pages(30)
        self.load("--max-pages", "20")
        self.assertIn("20 pages fetched", self.last_run()["note"])
        self.assertIn("12 left for the next load (stopped at --max-pages 20)", self.last_run()["note"])
        with Store(self.db) as store:
            self.assertEqual(store.pending(), 12)
        self.load()
        self.assertIn("32 judgments in the sitemap, 12 pages fetched", self.last_run()["note"])
        self.assertEqual(max(self.site.calls[u] for u in self.site.pages), 1)   # nothing fetched twice

    def test_max_minutes_stops_cleanly(self):
        self.site.add_pages(3)
        code, _ = self.load("--max-minutes", "0")
        self.assertEqual(code, 0)
        self.assertIn("0 pages fetched, 5 left for the next load (stopped at --max-minutes 0)",
                      self.last_run()["note"])

    def test_a_few_failed_pages_are_logged_and_retried_next_load(self):
        self.site.add_pages(20)
        self.site.failing.add(APPEAL)
        code, _ = self.load()
        self.assertEqual(code, 0)
        run_ = self.last_run()
        self.assertEqual(run_["status"], "ok")
        self.assertIn("21 pages fetched, 1 failed (retried next load)", run_["note"])
        with Store(self.db) as store:
            (failed,) = store.failures()
        self.assertEqual((failed["url"], failed["attempts"]), (APPEAL, 1))
        self.assertIn("503", failed["error"])
        self.site.failing.clear()
        self.load()
        self.assertIn("1 pages fetched", self.last_run()["note"])
        with Store(self.db) as store:
            self.assertEqual(store.failures(), [])

    def test_more_than_a_tenth_failing_fails_the_run(self):
        self.site.failing.add(APPEAL)
        with self.assertRaises(Exception):
            self.load()
        run_ = self.last_run()
        self.assertEqual(run_["status"], "error")
        self.assertIn("1 of 2 pages failed", run_["error"])
        self.assertIn("1 failed", run_["note"])
        with Store(self.db) as store:
            self.assertEqual(len(store.judgments()), 1)      # what was read is kept

    def test_a_missing_sitemap_fails_the_run(self):
        self.site.failing.add(SITEMAP)
        with self.assertRaises(Exception):
            self.load()
        self.assertIn("503", self.last_run()["error"])

    def test_robots_txt_is_obeyed(self):
        self.site.robots += "\nUser-agent: qdb-alt\nDisallow: /judgments/case-no-\n"
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("1 disallowed by robots.txt, not fetched", printed)
        self.assertEqual(self.site.calls[APPEAL], 0)
        self.assertEqual(self.site.calls[ROBOTS], 1)

    def test_robots_crawl_delay_is_the_least_pause(self):
        self.site.robots = (FIXTURES / "robots.txt").read_text(encoding="utf-8")
        with mock.patch("qicdrc_judgments.fetch.time.sleep") as sleep:
            self.load()
        self.assertGreater(max(c.args[0] for c in sleep.call_args_list), 9)

    def test_match_writes_features_and_leaves_unknowns_empty(self):
        self.load()
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,SUNRISE DATES FACTORY W.L.L,000000012345\n"
                        "C2,SUNSET DATES FACTORY,000000054321\n"
                        "C3,PEARL COAST TRADING AND CONTRACTING CO,000000011111\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(self.site, "--db", self.db, "match", str(book), "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("2 of 3 customers are a party, in 2 judgments", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        c1 = rows["C1"]
        self.assertEqual((c1["qicdrc_cases"], c1["qicdrc_as_claimant"], c1["qicdrc_as_defendant"],
                          c1["qicdrc_last_judgment_year"]), ("2", "1", "1", "2026"))
        self.assertEqual(c1["qicdrc_citations"], "[2025] QIC (F) 99; [2026] QIC (A) 3")
        self.assertEqual(c1["qicdrc_outcomes"], "default judgment; appeal dismissed")
        self.assertEqual(c1["qicdrc_matched_parties"], "Sunrise Dates Factory W.L.L; Sunrise Dates Factory WLL")
        self.assertEqual((rows["C3"]["qicdrc_cases"], rows["C3"]["qicdrc_as_defendant"]), ("1", "1"))
        c2 = rows["C2"]
        self.assertEqual((c2["qicdrc_cases"], c2["qicdrc_as_defendant"], c2["qicdrc_last_judgment_year"],
                          c2["qicdrc_citations"]), ("0", "0", "", ""))
        self.assertEqual(self.site.calls[SITEMAP], 1)      # match works offline from the database
        with Store(self.db) as store:
            self.assertEqual(store.runs(1)[0]["command"], "match")

    def test_match_before_load(self):
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("name\nX\n", encoding="utf-8")
        code, _ = run(self.site, "--db", self.db, "match", str(book))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
