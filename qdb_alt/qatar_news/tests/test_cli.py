"""The commands end to end against a stand-in for the four papers: a first
load, a second that fetches only what is new, a sitemap that arrives cut off,
a time limit, robots.txt obeyed and Crawl-delay kept on every request, and
matching a customer book."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

try:
    from qatar_news.cli import main
    from qatar_news.fetch import gt_search_url
    from qatar_news.robots import Robots
    from qatar_news.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_news.cli import main
    from qatar_news.fetch import gt_search_url
    from qatar_news.robots import Robots
    from qatar_news.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_site as fs  # noqa: E402

BOOK = ("customer_id,name,cr_number,search_name\n"
        "C1,SUNRISE DATES FACTORY W.L.L,000000012345,\n"
        "C2,DOHA BUS COMPANY,000000054321,\n"
        "C3,AL BAHAR TRADING AND CONTRACTING,000000011111,Blue Harbour Ice\n"
        "C4,PEARL COAST LAW FIRM W.L.L,000000022222,\n"
        "C5,DELTA CORPORATION,000000033333,\n")
DELAYS = {"www.gulf-times.com": 2, "www.qatar-tribune.com": 2, "thepeninsulaqatar.com": 10,
          "www.qna.org.qa": 2, "qna.org.qa": 2}
NEWS = ("news_gulf_times_articles", "news_headlines", "news_headlines_365d", "news_papers", "news_days_since_last",
        "news_negative_count", "news_name_generic")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.site = fs.FakeSite()
        self.site.failing.add(gt_search_url("Pearl Coast Law Firm"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(["--db", self.db, *argv], session=self.site, timer=self.site.timer)
        return code, out.getvalue()

    def load(self, *extra):
        return self.run_cli("load", *extra)

    def match(self) -> dict[str, dict[str, str]]:
        book = Path(self.tmp.name) / "book.csv"
        book.write_text(BOOK, encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, self.printed = self.run_cli("match", str(book), "--out", str(out))
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8-sig") as f:
            return {r["customer_id"]: r for r in csv.DictReader(f)}

    def last_run(self):
        with Store(self.db) as store:
            return store.runs(1)[0]

    # ---- load ---------------------------------------------------------------

    def test_first_load_reads_every_paper_and_skips_what_is_not_articles(self):
        code, printed = self.load()
        self.assertEqual(code, 0)
        for part in ["Gulf Times: 3 sitemaps read, 0 unchanged, 0 failed, 1 gone, 0 disallowed, 6 new articles",
                     "Qatar Tribune: 2 sitemaps read, 0 unchanged, 0 failed, 0 gone, 0 disallowed, 2 new articles",
                     "QNA: 4 sitemaps read, 0 unchanged, 0 failed, 0 gone, 0 disallowed, 6 new articles",
                     "The Peninsula: 3 sitemaps read, 0 unchanged, 0 failed, 0 gone, 0 disallowed, 9 new articles"]:
            self.assertIn(part, printed)
        for skipped in [fs.GT_SECTIONS, fs.GT_AUTHORS, fs.PENINSULA_NAV, fs.QNA_ARABIC,
                        f"{fs.GT}/sitemaps/0/0/sitemap_0.xml?v=1.1"]:
            self.assertEqual(self.site.calls[skipped], 0, skipped)
        with Store(self.db) as store:
            urls = {r["url"]: dict(r) for r in store.articles()}
            run = store.runs(1)[0]
        self.assertEqual((run["status"], run["rows"]), ("ok", 23))
        self.assertIn(f"{fs.GT}/article/700001/business/sunrise-dates-factory-opens-new-production-line", urls)
        self.assertFalse(any(u.count("https://") > 1 for u in urls))          # doubled addresses undone
        self.assertEqual(urls[f"{fs.PENINSULA}/article/14/05/2024/sunrise-dates-factory-fined-for-labelling-"
                              f"violations"]["date"], "2024-05-14")

    def test_a_second_load_fetches_only_the_indexes_the_current_month_and_qna_page_one(self):
        self.load()
        code, printed = self.load()
        self.assertIn("Gulf Times: 1 sitemaps read, 2 unchanged, 0 failed, 1 gone", printed)
        self.assertIn("QNA: 2 sitemaps read, 2 unchanged", printed)
        self.assertIn("The Peninsula: 1 sitemaps read, 2 unchanged", printed)
        calls = self.site.calls
        self.assertEqual((calls[fs.GT_INDEX], calls[fs.GT_ARCHIVE_INDEX], calls[fs.GT_NOW], calls[fs.PENINSULA_NOW]),
                         (2, 2, 2, 2))
        self.assertEqual((calls[fs.GT_OLD], calls[fs.GT_ARCHIVE], calls[fs.TRIBUNE_OLD], calls[fs.PENINSULA_OLD],
                          calls[fs.QNA_PAGE.format(2)]), (1, 1, 1, 1, 1))
        self.assertEqual(self.last_run()["rows"], 0)

    def test_a_month_the_index_says_changed_is_fetched_again(self):
        self.load()
        self.site.pages[fs.GT_INDEX] = self.site.pages[fs.GT_INDEX].replace("2024-06-02T10:00:00", "2026-01-09T10:00:00")
        self.load()
        self.assertEqual(self.site.calls[fs.GT_OLD], 2)

    def test_new_qna_items_push_the_pages_along_and_the_next_page_is_read(self):
        self.load()
        self.site.qna_new = [fs.qna_item("new-item-at-the-top", fs.TODAY.isoformat())]
        code, printed = self.load("--papers", "qna")
        self.assertIn("QNA: 3 sitemaps read, 1 unchanged, 0 failed, 0 gone, 0 disallowed, 1 new articles", printed)
        self.assertEqual((self.site.calls[fs.QNA_PAGE.format(2)], self.site.calls[fs.QNA_PAGE.format(3)]), (2, 1))

    def test_a_cut_off_sitemap_is_not_kept_and_is_read_again_next_time(self):
        self.site.cut.add(fs.PENINSULA_CUT)
        with self.assertRaises(Exception):
            self.load()
        run = self.last_run()
        self.assertEqual(run["status"], "error")
        self.assertIn("cut off", run["error"])
        self.assertIn("The Peninsula: 2 sitemaps read, 0 unchanged, 1 failed", run["note"])
        self.assertEqual(self.site.calls[fs.PENINSULA_CUT], 2)                # tried once more
        with Store(self.db) as store:
            self.assertEqual(store.sitemaps()[fs.PENINSULA_CUT]["complete"], 0)
            self.assertNotIn("a whole april headline", [r["title"] for r in store.articles()])
        self.site.cut.clear()
        code, printed = self.load()
        self.assertIn("The Peninsula: 2 sitemaps read, 1 unchanged, 0 failed", printed)   # the current month, and it

    def test_a_time_limit_stops_cleanly_and_the_next_load_continues(self):
        code, printed = self.load("--max-minutes", "0.75")
        self.assertEqual(code, 0)
        self.assertIn("still to read; run load again", printed)
        self.assertEqual(self.last_run()["status"], "stopped")
        self.load()
        self.assertEqual(self.last_run()["status"], "ok")
        with Store(self.db) as store:
            self.assertEqual(sum(store.paper_counts().values()), 23)
        self.assertEqual(self.site.calls[fs.PENINSULA_OLD], 1)

    def test_only_the_papers_asked_for(self):
        code, printed = self.load("--papers", "qatar_tribune,qna")
        self.assertNotIn("Gulf Times", printed.split("\n")[0])
        self.assertEqual(self.site.calls[fs.GT_INDEX] + self.site.calls[fs.PENINSULA_INDEX], 0)

    # ---- robots.txt ---------------------------------------------------------

    def test_every_request_is_allowed_by_robots_txt_and_paced_by_its_crawl_delay(self):
        self.load()
        self.match()
        first_robots, last = {}, defaultdict(lambda: None)
        for url, t in self.site.log:
            host = urlsplit(url).netloc
            if urlsplit(url).path == "/robots.txt":
                first_robots.setdefault(host, t)
            else:
                self.assertIn(host, first_robots, f"{url} before {host}/robots.txt")
                self.assertTrue(Robots(self.site.robots[host], "qdb-alt").allowed(url), url)
            if last[host] is not None:
                self.assertGreaterEqual(t - last[host], DELAYS[host], url)
            last[host] = t
        self.assertEqual(set(first_robots), set(DELAYS))
        # once per host and run: five hosts for load, Gulf Times again for match
        self.assertEqual(sum(1 for u, _ in self.site.log if u.endswith("/robots.txt")), 6)

    def test_a_sitemap_robots_txt_forbids_is_never_requested(self):
        self.site.robots["www.qatar-tribune.com"] += "Disallow: /sitemaps/2024/\n"
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("Qatar Tribune: 1 sitemaps read, 0 unchanged, 0 failed, 0 gone, 1 disallowed", printed)
        self.assertEqual(self.site.calls[fs.TRIBUNE_OLD], 0)

    def test_a_host_whose_robots_txt_cannot_be_read_is_not_read(self):
        self.site.failing.add(f"{fs.QNA}/robots.txt")
        with self.assertRaises(Exception):
            self.load()
        asked = [u for u, _ in self.site.log if urlsplit(u).netloc == "qna.org.qa"]
        self.assertEqual(asked, [f"{fs.QNA}/robots.txt"] * 2)               # robots.txt, tried once more; nothing else
        self.assertIn("QNA: 0 sitemaps read, 0 unchanged, 4 failed", self.last_run()["note"])

    def test_a_search_robots_txt_forbids_is_not_made_and_left_empty(self):
        self.site.robots["www.gulf-times.com"] += "\nUser-agent: qdb-alt\nDisallow: /search\n"
        self.load()
        rows = self.match()
        self.assertFalse([u for u, _ in self.site.log if "/search" in u])
        self.assertEqual(rows["C1"]["news_gulf_times_articles"], "")
        self.assertEqual(rows["C1"]["news_headlines"], "4")
        self.assertIn("0 fetched, 0 cached, 4 failed", self.printed)

    # ---- match --------------------------------------------------------------

    def test_match_writes_features(self):
        self.load()
        rows = self.match()
        self.assertIn("3 of 5 customers with news, 1 with a generic name (not counted); "
                      "Gulf Times search: 3 fetched, 0 cached, 1 failed", self.printed)
        news = lambda c: tuple(rows[c][k] for k in NEWS)
        #                         GT   heads 365d papers days neg generic
        self.assertEqual(news("C1"), ("12", "4", "1", "4", "0", "1", "0"))
        self.assertEqual(news("C2"), ("", "", "", "", "", "", "1"))           # generic: nothing counted
        self.assertEqual(news("C3"), ("0", "0", "0", "0", "", "0", "0"))      # searched, nothing: zeros
        self.assertEqual(news("C4")[:2], ("", "1"))                           # the search failed: empty, not 0
        self.assertEqual(news("C5")[:2], ("0", "1"))                          # "Delta Corporation", not "Delta variant"
        self.assertTrue(rows["C1"]["example_headlines"].startswith(f"{fs.TODAY.isoformat()} QNA: sunrise dates "))
        self.assertEqual(rows["C3"]["search_name"], "Blue Harbour Ice")
        self.assertEqual(rows["C2"]["gulf_times_search_url"], "")
        self.assertEqual(self.site.calls[gt_search_url("Doha Bus")], 0)
        self.assertEqual(self.site.calls[fs.GT_INDEX], 1)                     # match reads no sitemap

    def test_a_second_match_reuses_the_searches(self):
        self.load()
        self.match()
        self.match()
        self.assertIn("Gulf Times search: 0 fetched, 3 cached, 1 failed", self.printed)
        self.assertEqual(self.site.calls[gt_search_url("Sunrise Dates Factory")], 1)
        with Store(self.db) as store:
            self.assertEqual([r["command"] for r in store.runs()], ["match", "match", "load"])

    def test_match_before_load_says_so(self):
        book = Path(self.tmp.name) / "book.csv"
        book.write_text(BOOK, encoding="utf-8")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(["--db", self.db, "match", str(book)], session=self.site, timer=self.site.timer)
        self.assertEqual(code, 2)
        self.assertIn("run `load` first", err.getvalue())


if __name__ == "__main__":
    unittest.main()
