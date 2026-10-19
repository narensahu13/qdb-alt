"""Reading search results and listing pages.

The fixtures are made-up pages in the site's real layout: a search with three
results (one of them another company), a search spread over two result pages,
a search with no result, three listing pages (a full one, a short one, one
with a commented-out phone number) and a block page.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qatar_online_directory.match import pick
    from qatar_online_directory.namematch import same_company
    from qatar_online_directory.parse import PageError, parse_company, parse_search
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_online_directory.match import pick
    from qatar_online_directory.namematch import same_company
    from qatar_online_directory.parse import PageError, parse_company, parse_search

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class Search(unittest.TestCase):
    def test_every_result_with_its_id_slug_and_name(self):
        listings, pages = parse_search(page("search_sunrise.html"))
        self.assertEqual(pages, 1)
        self.assertEqual([(l["listing_id"], l["name"]) for l in listings],
                         [(9001, "New Sunrise Dates Factory"), (9002, "Sunrise Dates Factory WLL"),
                          (9003, "Sunrise Date Factory")])
        self.assertEqual(listings[1]["url"],
                         "https://qataronlinedirectory.com/company-details/9002/Sunrise-Dates-Factory-WLL")

    def test_html_entities_in_names_and_slugs(self):
        (l,), _ = parse_search(page("search_blue_2.html"))
        self.assertEqual((l["name"], l["slug"]), ("Blue Harbour Ice & Water", "Blue-Harbour-Ice-&-Water"))

    def test_the_number_of_result_pages_is_read_from_the_pagination(self):
        self.assertEqual(parse_search(page("search_blue_1.html"))[1], 2)

    def test_no_result_is_an_empty_list(self):
        self.assertEqual(parse_search(page("search_none.html")), ([], 1))

    def test_a_page_that_is_not_a_result_page_is_refused_not_read_as_empty(self):
        with self.assertRaises(PageError):
            parse_search(page("block_page.html"))


class Picking(unittest.TestCase):
    def test_another_brand_in_front_is_dropped_and_near_spellings_are_kept(self):
        listings, _ = parse_search(page("search_sunrise.html"))
        hits = pick("Sunrise Dates Factory", listings)
        self.assertEqual([h["listing_id"] for h in hits], [9002, 9003])      # not "New Sunrise ..."
        self.assertEqual(hits[0]["score"], 100.0)

    def test_only_listings_close_to_the_best_name_are_kept(self):
        listings = [{"listing_id": 1, "name": "Qatar Steel Industries Factory Mesaieed Branch"},   # 79
                    {"listing_id": 2, "name": "Qatar Steel Industries Factory WLL"}]               # 100
        # both are the company by the name rule; the branch is just much further from the name
        self.assertTrue(all(same_company("Qatar Steel Industries Factory", l["name"]) for l in listings))
        self.assertEqual([h["listing_id"] for h in pick("Qatar Steel Industries Factory", listings)], [2])

    def test_nothing_that_is_not_the_company(self):
        listings, _ = parse_search(page("search_blue_1.html"))
        self.assertEqual(pick("Blue Harbour Ice", listings), [])


class Company(unittest.TestCase):
    def test_full_listing(self):
        c = parse_company(page("company_9002.html"))
        self.assertEqual(c, {
            "name": "Sunrise Dates Factory WLL", "city": "Doha", "has_po_box": 1, "has_phone": 1,
            "categories": ["DATES PROCESSING AND PACKING", "FOOD PRODUCT MANUFACTURERS"],   # repeated once
            "brands": ["sunrise Date Syrup"]})

    def test_short_listing(self):
        c = parse_company(page("company_9003.html"))
        self.assertEqual((c["city"], c["has_po_box"], c["has_phone"]), ("Al Khor", 0, 1))

    def test_commented_out_parts_are_ignored(self):
        c = parse_company(page("company_9102.html"))
        self.assertEqual((c["has_po_box"], c["has_phone"]), (1, 0))

    def test_contact_values_are_not_kept(self):
        kept = repr([parse_company(page(f"company_{n}.html")) for n in (9002, 9003, 9102)])
        for private in ("44000001", "4400 0003", "44000002", "99001", "99002"):
            self.assertNotIn(private, kept)

    def test_a_page_that_is_not_a_listing_is_refused(self):
        for name in ("block_page.html", "search_none.html"):
            with self.assertRaises(PageError):
                parse_company(page(name))


if __name__ == "__main__":
    unittest.main()
