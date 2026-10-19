"""The name rules. Every case here is a real pair from a run over QDB's client
list: the ones that must match were missed by a looser rule or a stricter one,
the ones that must not were wrong matches that reached a feature table."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from gleif_lei.namematch import best_match, brand_match, same_company, search_name
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gleif_lei.namematch import best_match, brand_match, same_company, search_name


class SameCompany(unittest.TestCase):
    def test_same(self):
        for me, candidate in [
            ("Qatar Steel Industries Factory", "QATAR STEEL INDUSTRIES FACTORY W.L.L"),
            ("West Bay Ice", "Westbay Ice & Water Factory"),                     # glued
            ("West Bay Ice", "Aseel water (westbay ice &water factory)"),        # the name in brackets
            ("Qatar Aluminum Extrusion", "Qatar Aluminium Extrusion Co"),        # one letter
            ("Extra Co Steel", "Extraco Steel Factory"),                         # abbreviation
            ("Shail Shipping & Maritime Services", "S'hail Shipping and Maritime Services"),
            ("Doha Academy", "Doha Academy Al Waab Campus"),                     # a campus
            ("Delta", "Delta Corporation"),
        ]:
            self.assertTrue(same_company(me, candidate), (me, candidate))

    def test_not_same(self):
        for me, candidate in [
            ("National Plastic Factory", "Qatar International Plastic Factory"),  # NATIONAL in INTERNATIONAL
            ("Crystal Dry Ice Factory", "Crystal Dry Cleaning Services"),         # ICE in SERVICES
            ("Noor Plastic", "Abdulnoor Plastic"),
            ("Qatar Aluminum Extrusion", "Abdul Noor Aluminum Extrusion"),        # a foreign brand in front
            ("Qatar Technical International Company", "Qatar Technical Inspection Co."),
            ("Shail", "Suhail Industrial Holding"),
        ]:
            self.assertFalse(same_company(me, candidate), (me, candidate))

    def test_short_words_are_not_a_brand_and_one_word_names_take_no_extra_words(self):
        self.assertFalse(same_company("La Casa Industry", "La Mia Casa"))
        self.assertFalse(same_company("Doha Academy", "Qatar Academy Doha"))
        self.assertTrue(same_company("Qatar Detergents", "Qatar Detergent Company (QDC)"))
        self.assertFalse(same_company("Padel In Sports", "Padel - Agon Sports Centre"))   # IN must be there
        self.assertTrue(same_company("Gulf Grc And Trading", "Gulf G. R. C. Trading"))        # letters apart


class Brand(unittest.TestCase):
    def test_spelling_variants_of_the_brand(self):
        self.assertTrue(brand_match("Al Hudaifi Steel", "Al Hodaifi Steel"))
        self.assertTrue(brand_match("Al Rahalat Caravan Factory", "Al Rehlat Caravans Factory"))

    def test_a_different_brand_sharing_the_other_words(self):
        for me, candidate in [("Al Mana Switchgear", "Al Dana Switchgear"),
                              ("Floral Detergent Factory", "Global Detergent Factory"),
                              ("Al Wajba Dairy And Juices Factory", "Al Wahaj Dairy and Juice Factory"),
                              ("Qatar International Facory For Plastics", "Qatar National Plastic Factory")]:
            self.assertFalse(brand_match(me, candidate), (me, candidate))
            self.assertEqual(best_match(me, [candidate])[0], None)


class SearchName(unittest.TestCase):
    def test_legal_forms_and_brackets_are_dropped(self):
        self.assertEqual(search_name("QATAR BELGIUM ALUMINUM CO L.L.C"), "Qatar Belgium Aluminum")
        self.assertEqual(search_name("Qatar Steel Industries Factory (QSIF)"), "Qatar Steel Industries Factory")
        self.assertEqual(search_name("Delta Corporation"), "Delta Corporation")

    def test_a_name_wrapped_at_forty_characters_is_rejoined(self):
        self.assertEqual(search_name("BU MATAR GERMAN FACTORY FOR PLASTIC PROD UCTS"),
                         "Bu Matar German Factory For Plastic Products")


if __name__ == "__main__":
    unittest.main()
