"""robots.txt by RFC 9309: longest match wins, Allow wins a tie, wildcards and
'$', merged groups, a byte-order mark -- and what that means on each paper:
the sitemaps and Gulf Times' search are allowed; Gulf Times' archive and
paged lists, the other papers' searches and QNA's /umbraco/ are not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qatar_news.robots import Robots
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_news.robots import Robots

ROBOTS = Path(__file__).resolve().parents[1] / "fixtures" / "robots"
SITE = "https://example.qa"


def site(name: str, bom: bool = False) -> Robots:
    text = (ROBOTS / f"{name}.txt").read_text(encoding="utf-8")
    return Robots(("\ufeff" if bom else "") + text, "qdb-alt")


class Rfc9309(unittest.TestCase):
    def test_longest_match_wins_and_allow_wins_a_tie(self):
        r = Robots("User-agent: *\nDisallow: /a/\nAllow: /a/b\nDisallow: /a/b/c\nAllow: /x\nDisallow: /x\n", "bot")
        self.assertFalse(r.allowed(SITE + "/a/z"))
        self.assertTrue(r.allowed(SITE + "/a/b/z"))
        self.assertFalse(r.allowed(SITE + "/a/b/c/z"))
        self.assertTrue(r.allowed(SITE + "/x"))

    def test_wildcards_and_end_anchor(self):
        r = Robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?q=*&\n", "bot")
        self.assertFalse(r.allowed(SITE + "/files/a.pdf"))
        self.assertTrue(r.allowed(SITE + "/files/a.pdf?x=1"))
        self.assertFalse(r.allowed(SITE + "/s?q=1&b=2"))
        self.assertTrue(r.allowed(SITE + "/s?q=1"))

    def test_groups_for_the_same_agent_are_merged_and_beat_the_star_group(self):
        r = Robots("User-agent: *\nDisallow: /\n\nUser-agent: Bot\nDisallow: /a\n\n"
                   "User-agent: other\nUser-agent: bot\nDisallow: /b\n", "bot")
        self.assertTrue(r.allowed(SITE + "/c"))
        self.assertFalse(r.allowed(SITE + "/a"))
        self.assertFalse(r.allowed(SITE + "/b"))

    def test_no_rules_allows_everything_and_robots_txt_is_always_allowed(self):
        self.assertTrue(Robots("", "bot").allowed(SITE + "/anything"))
        self.assertTrue(Robots("User-agent: *\nDisallow: /\n", "bot").allowed(SITE + "/robots.txt"))

    def test_percent_encoding_is_compared_decoded(self):
        r = Robots("User-agent: *\nDisallow: /caf%C3%A9\n", "bot")
        self.assertFalse(r.allowed(SITE + "/café/menu"))

    def test_a_byte_order_mark_does_not_hide_the_first_group(self):
        r = Robots("\ufeffUser-agent: *\nDisallow: /umbraco/\n", "bot")
        self.assertFalse(r.allowed(SITE + "/umbraco/api/x"))

    def test_a_group_named_after_part_of_our_token_is_not_ours(self):
        # The Peninsula shuts out 'es' and 'Mozilla'; 'qdb-alt' is neither
        r = Robots("User-agent: *\nDisallow: /search/\n\nUser-agent: es\nDisallow: /\n", "qdb-alt")
        self.assertTrue(r.allowed(SITE + "/sitemap.xml"))


class GulfTimes(unittest.TestCase):
    r = site("gulf_times")
    ROOT = "https://www.gulf-times.com"

    def test_the_sitemaps_and_the_search_are_allowed(self):
        for path in ["/sitemaps/sitemap_0.xml", "/sitemaps/2026/10/sitemap_0.xml?v=1.1",
                     "/sitemaps/sitemap_article_archive.xml?v=1", "/sitemaps/2015/5/sitemap_archive.xml",
                     "/search?query=Qatar+Steel+Industries+Factory&match=exact", "/robots.txt"]:
            self.assertTrue(self.r.allowed(self.ROOT + path), path)

    def test_the_archive_pages_rss_and_paged_lists_are_not(self):
        for path in ["/archive/2015/05", "/rss", "/search/page/2?query=x", "/business/page/3", "/ajax/more",
                     "/article.php?id=1", "/print/737001"]:
            self.assertFalse(self.r.allowed(self.ROOT + path), path)

    def test_the_crawl_delay_from_the_second_star_group(self):
        self.assertEqual(self.r.crawl_delay, 2)


class QatarTribune(unittest.TestCase):
    r = site("qatar_tribune")
    ROOT = "https://www.qatar-tribune.com"

    def test_sitemaps_yes_search_no(self):
        self.assertTrue(self.r.allowed(self.ROOT + "/sitemaps/2026/10/sitemap_0.xml?v=1.1"))
        self.assertTrue(self.r.allowed(self.ROOT + "/sitemaps/2018/5/sitemap_archive.xml"))
        for path in ["/search?query=x", "/search", "/searchresults", "/morearticles/2"]:
            self.assertFalse(self.r.allowed(self.ROOT + path), path)
        self.assertIsNone(self.r.crawl_delay)


class Peninsula(unittest.TestCase):
    r = site("peninsula")
    ROOT = "https://thepeninsulaqatar.com"

    def test_sitemaps_yes_search_no(self):
        for path in ["/sitemap.xml", "/sitemap_articles-2026-10.xml"]:
            self.assertTrue(self.r.allowed(self.ROOT + path), path)
        for path in ["/search/qatar", "/?q=search/qatar", "/xml/feed", "/files/list.xlsx"]:
            self.assertFalse(self.r.allowed(self.ROOT + path), path)

    def test_crawl_delay_ten_seconds(self):
        self.assertEqual(self.r.crawl_delay, 10)


class Qna(unittest.TestCase):
    r = site("qna", bom=True)

    def test_english_lists_yes_umbraco_search_no(self):
        self.assertTrue(self.r.allowed("https://www.qna.org.qa/sitemap.xml"))
        self.assertTrue(self.r.allowed("https://qna.org.qa/CuiNormalNews?Language=en&Page=3&PageSize=5000"))
        self.assertFalse(self.r.allowed("https://www.qna.org.qa/umbraco/api/Home/ACSSearch?q=x"))


if __name__ == "__main__":
    unittest.main()
