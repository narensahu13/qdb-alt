"""Reading the papers' sitemaps and Gulf Times' search page. The sitemaps
are in each paper's real shape with made-up headlines."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qatar_news import parse
    from qatar_news.parse import PAPERS, Incomplete
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_news import parse
    from qatar_news.parse import PAPERS, Incomplete

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_site as fs  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class Sitemaps(unittest.TestCase):
    def test_a_whole_sitemap_is_read_and_a_cut_one_is_refused(self):
        whole = fs.FakeSite().pages[fs.GT_OLD].encode("utf-8")
        self.assertEqual(parse.read_sitemap(whole)[0], "urls")
        for cut in (whole[: len(whole) // 2], whole[:-12], b"<html><body>Bad gateway</body></html>", b""):
            with self.assertRaises(Incomplete):
                parse.read_sitemap(cut)

    def test_an_index_cut_just_after_an_entry_is_refused(self):
        text = fs.FakeSite().pages[fs.GT_INDEX]
        cut = text[: text.rindex("</sitemap>") + len("</sitemap>")]
        with self.assertRaises(Incomplete):
            parse.read_sitemap(cut.encode("utf-8"))

    def test_gulf_times_index_months_archive_and_what_is_skipped(self):
        _, root = parse.read_sitemap(fs.FakeSite().pages[fs.GT_INDEX].encode("utf-8"))
        kids = parse.children(PAPERS["gulf_times"], root)
        self.assertEqual([(c.url, c.kind, c.period) for c in kids],
                         [(fs.GT_1970, "month", "1970-01"), (fs.GT_OLD, "month", "2024-05"),
                          (fs.GT_NOW, "month", f"{fs.Y}-{fs.M:02d}"), (fs.GT_ARCHIVE_INDEX, "index", None)])

    def test_gulf_times_doubled_addresses_and_titles_from_the_address(self):
        _, root = parse.read_sitemap(fs.FakeSite().pages[fs.GT_OLD].encode("utf-8"))
        first = parse.articles(PAPERS["gulf_times"], root)[0]
        self.assertEqual(first, {"paper": "Gulf Times", "date": "2024-05-12",
                                 "url": "https://www.gulf-times.com/article/700001/business/"
                                        "sunrise-dates-factory-opens-new-production-line",
                                 "title": "sunrise dates factory opens new production line"})

    def test_peninsula_titles_and_the_date_in_the_address(self):
        _, root = parse.read_sitemap((FIXTURES / "peninsula_articles.xml").read_bytes())
        rows = parse.articles(PAPERS["peninsula"], root)
        self.assertEqual(len(rows), 7)
        # the publication date says 2021 (the day the old site was moved); the address says 14 May 2024
        self.assertEqual((rows[0]["title"], rows[0]["date"]),
                         ("Sunrise Dates Factory fined for labelling violations", "2024-05-14"))
        self.assertEqual(rows[4]["title"], "Free bus shuttle for fans at Lusail & Al Bayt")

    def test_peninsula_only_monthly_article_sitemaps(self):
        _, root = parse.read_sitemap(fs.FakeSite().pages[fs.PENINSULA_INDEX].encode("utf-8"))
        self.assertEqual([c.url for c in parse.children(PAPERS["peninsula"], root)],
                         [fs.PENINSULA_NOW, fs.PENINSULA_OLD, fs.PENINSULA_CUT])

    def test_qna_english_lists_only_with_their_page_numbers(self):
        site = fs.FakeSite()
        _, root = parse.read_sitemap(site._qna(fs.QNA_INDEX).encode("utf-8"))
        kids = parse.children(PAPERS["qna"], root)
        self.assertEqual([(c.feed, c.page) for c in kids],
                         [("CuiNormalNews", 1), ("CuiNormalNews", 2), ("CuiNormalNews", 3), ("CuiPhotoAlbums", 1)])
        self.assertEqual(kids[0].url, fs.QNA_PAGE.format(1))
        kind, page = parse.read_sitemap(site._qna(fs.QNA_PAGE.format(1)).encode("utf-8"))
        rows = parse.articles(PAPERS["qna"], page)
        self.assertEqual(kind, "urls")
        self.assertEqual((rows[0]["title"], rows[0]["date"]),
                         ("sunrise dates factory signs export deal", fs.TODAY.isoformat()))

    def test_a_stray_control_character_does_not_spoil_the_month(self):
        page = fs.urlset([(f"{fs.GT}/article/1/x/a-headline", "2015-07-01")]).replace("a-headline", "a-\x08headline")
        _, root = parse.read_sitemap(page.encode("utf-8"))
        self.assertEqual(parse.articles(PAPERS["qatar_tribune"], root)[0]["title"], "a headline")

    def test_an_epoch_date_is_no_date(self):
        self.assertIsNone(parse.article_date(PAPERS["gulf_times"], "u", "", "1970-01-01T00:00:00+03:00"))
        self.assertEqual(parse.article_date(PAPERS["gulf_times"], "u", "", "2011-01-02T00:00:00+03:00"),
                         "2011-01-02")

    def test_title_from_url(self):
        self.assertEqual(parse.title_from_url("https://qna.org.qa/en/News-Area/News/2024-6/11/"
                                              "0021-russian-su-34-warplane%20crashes,-crew-killed"),
                         "0021 russian su 34 warplane crashes, crew killed")


class GulfTimesSearch(unittest.TestCase):
    def test_the_count_and_the_titles(self):
        count, titles = parse.gt_search((FIXTURES / "gt_search.html").read_text(encoding="utf-8"))
        self.assertEqual(count, 12)
        self.assertEqual(titles, ["Sunrise Dates Factory expands into Oman",
                                  "Sunrise Dates Factory fined for labelling violations",
                                  "Date season opens at Souq Waqif & Al Wakrah"])

    def test_no_results_is_zero(self):
        self.assertEqual(parse.gt_search((FIXTURES / "gt_search_none.html").read_text(encoding="utf-8")), (0, []))

    def test_a_page_without_the_count_is_not_zero(self):
        with self.assertRaises(ValueError):
            parse.gt_search("<html><body>Please wait while we check your browser</body></html>")

    def test_a_thousands_separator(self):
        self.assertEqual(parse.gt_search('<span class="search-count">(1,204 articles)</span>')[0], 1204)


if __name__ == "__main__":
    unittest.main()
