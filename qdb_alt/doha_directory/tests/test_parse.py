"""Reading a listing page and the sitemap. The fixture page is made up, in the
site's real markup: schema.org itemprops for the name, category path, rating,
opening hours, address, phone, e-mail and website, then social links and tags."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from doha_directory.parse import founding_year, parse_listing, sitemap, slug_name
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from doha_directory.parse import founding_year, parse_listing, sitemap, slug_name

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
URL = "https://www.doha.directory/sunrise-dates-factory/i/2201"


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = parse_listing((FIXTURES / "listing_page.html").read_text(encoding="utf-8"), URL)

    def test_name_id_and_category_path(self):
        self.assertEqual((self.f["name"], self.f["listing_id"], self.f["slug_name"]),
                         ("Sunrise Dates Factory", 2201, "sunrise dates factory"))
        self.assertEqual(self.f["category"], "Food Manufacturing")
        self.assertEqual(self.f["category_path"], "Services / Food & Beverages / Food Manufacturing")

    def test_rating_and_reviews(self):
        self.assertEqual((self.f["rating"], self.f["reviews"]), (4.4, 37))

    def test_contacts_are_flags_and_the_website_is_kept(self):
        self.assertEqual((self.f["has_phone"], self.f["has_email"]), (1, 1))
        self.assertEqual(self.f["website"], "https://sunrisedates.example/")
        self.assertEqual(self.f["address"], "Street 12, New Industrial Area, Doha, Qatar")
        self.assertNotIn("info@sunrisedates.example", " ".join(str(v) for v in self.f.values()))

    def test_social_links_with_an_address_only(self):
        self.assertEqual(self.f["social"], "facebook,linkedin")        # the empty twitter link is not one

    def test_open_days_founding_year_and_tags(self):
        self.assertEqual(self.f["open_days"], 6)
        self.assertEqual(self.f["founded_year"], 2015)
        self.assertEqual(self.f["tags"], "Dates; Date paste")
        self.assertGreater(self.f["description_chars"], 100)

    def test_a_bare_listing(self):
        f = parse_listing('<h1 itemprop="name">Blue Harbour Ice</h1>'
                          '<span itemprop="department">Ice Suppliers</span>',
                          "https://www.doha.directory/blue-harbour-ice/i/2202")
        self.assertEqual((f["category"], f["rating"], f["reviews"], f["has_phone"], f["has_email"], f["website"],
                          f["social"], f["open_days"], f["founded_year"]),
                         ("Ice Suppliers", None, 0, 0, 0, None, None, None, None))


class FoundingYear(unittest.TestCase):
    def test_a_stated_year(self):
        self.assertEqual(founding_year("Established in 1980, Qatar Steel Industries Factory became ..."), 1980)

    def test_no_year_or_a_year_ahead(self):
        self.assertIsNone(founding_year("A leading supplier in Qatar since many years."))
        self.assertIsNone(founding_year("Founded in 2999 by time travellers."))


class Sitemap(unittest.TestCase):
    def test_lists_listing_pages_with_dates_and_skips_news_and_search_pages(self):
        pages = sitemap((FIXTURES / "sitemap.xml").read_text(encoding="utf-8"))
        self.assertEqual([u.rsplit("/", 1)[-1] for u, _ in pages], ["2201", "2202", "2203", "2204"])
        self.assertEqual(pages[0], (URL, "2024-09-03T06:35:16+04:00"))

    def test_slug_name(self):
        self.assertEqual(slug_name("https://www.doha.directory/------pearl-coast-law-firm/i/2203"),
                         "pearl coast law firm")
        self.assertEqual(slug_name("https://www.doha.directory/some-news/2210/n"), "")


if __name__ == "__main__":
    unittest.main()
