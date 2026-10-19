"""robots.txt by RFC 9309: longest match wins, Allow wins a tie, wildcards and
'$', merged groups -- and what that means on qicdrc.gov.qa: the judgment pages
and the sitemap are allowed, the paged and sorted lists are not."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qicdrc_judgments.robots import Robots
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qicdrc_judgments.robots import Robots

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SITE = "https://www.qicdrc.gov.qa"


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


class TheSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Robots((FIXTURES / "robots.txt").read_text(encoding="utf-8"), "qdb-alt")

    def test_what_this_package_reads_is_allowed(self):
        for path in ["/sitemap.xml", "/judgments/case-no-2-2020-2020-qic-f-7",
                     "/judgments/ctfic00182021-1-daruna-real-estate-brokerage-co-llc-2-sheikh-nasser-bin"]:
            self.assertTrue(self.r.allowed(SITE + path), path)

    def test_the_paged_and_sorted_lists_are_not(self):
        for path in ["/judgements/list?page=3&items_per_page=10", "/judgments?k=83&page=2", "/judgments?sort=date",
                     "/judgments?order=asc", "/search?search_api_fulltext=bank", "/admin/content"]:
            self.assertFalse(self.r.allowed(SITE + path), path)

    def test_crawl_delay(self):
        self.assertEqual(self.r.crawl_delay, 10)


if __name__ == "__main__":
    unittest.main()
