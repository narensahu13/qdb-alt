"""Reading a factory page and the sitemap. The fixture page is made up, in the
site's real markup: labelled Address / Sector lines, the description, then
the "Our Website" and "Our Product" links."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from made_in_qatar.parse import founding_year, parse_factory, sitemap
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from made_in_qatar.parse import founding_year, parse_factory, sitemap

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
URL = "https://madeinqatar.qa/factories/sunrise-dates-factory/"


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = parse_factory((FIXTURES / "factory_page.html").read_text(encoding="utf-8"), URL)

    def test_name_comes_from_the_page_title(self):
        self.assertEqual(self.f["name"], "Sunrise Dates Factory (SDF)")
        self.assertEqual(self.f["slug"], "sunrise-dates-factory")

    def test_labelled_fields_on_one_line_or_split_over_two(self):
        self.assertEqual(self.f["address"], "Street 12, New Industrial Area, Doha – Qatar")
        self.assertEqual(self.f["sector"], "Manufacture of food products")

    def test_founding_year_website_and_products(self):
        self.assertEqual(self.f["founded_year"], 2015)
        self.assertEqual(self.f["website"], "https://sunrisedates.qa/")
        self.assertEqual(self.f["products"], 2)

    def test_scripts_and_footer_are_not_part_of_the_description(self):
        self.assertGreater(self.f["description_chars"], 100)
        self.assertLess(self.f["description_chars"], 300)


class FoundingYear(unittest.TestCase):
    def test_a_stated_year(self):
        self.assertEqual(founding_year(["Operational since 2019, the company makes popcorn."]), 2019)

    def test_a_timeline_gives_its_first_year_not_the_step_that_says_established(self):
        lines = ["1976", "First machines ordered from Italy.", "1977", "Fully founded in Abu Sidra.",
                 "2008", "PE sheets date established", "2013", "Moved to the New Industrial Area."]
        self.assertEqual(founding_year(lines), 1976)

    def test_years_inside_sentences_are_not_a_timeline(self):
        lines = ["Khalifa Steel is an ISO 9001:2015 certified enterprise.",
                 "Since its establishment in 2009, it has grown.", "In line with Qatar Vision 2030."]
        self.assertEqual(founding_year(lines), 2009)

    def test_no_year(self):
        self.assertIsNone(founding_year(["A leading manufacturer of quality products in Qatar."]))


class Sitemap(unittest.TestCase):
    def test_lists_factory_pages_with_dates_and_skips_the_archive_page(self):
        pages = sitemap((FIXTURES / "factories_sitemap.xml").read_text(encoding="utf-8"))
        self.assertEqual(pages, [(URL, "2024-09-03T06:35:16+00:00"),
                                 ("https://madeinqatar.qa/factories/blue-harbour-ice/", "2025-01-10T10:00:00+00:00")])


if __name__ == "__main__":
    unittest.main()
