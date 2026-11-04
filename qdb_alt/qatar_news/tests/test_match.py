"""Finding a name in headlines: the phrase, the narrowing anchor word, the
common-word rule for short generic names, and the features of one customer."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

try:
    from qatar_news.match import (Headlines, anchor, common_words, is_generic, match_one, mentions, phrase,
                                  words)
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_news.match import (Headlines, anchor, common_words, is_generic, match_one, mentions, phrase,
                                  words)

PENINSULA_TITLES = ["New bus routes link Lusail", "Karwa adds electric bus fleet", "School bus safety drive",
                    "Free bus shuttle for fans", "Metro and bus ridership rises", "Solar plant opens",
                    "Barzan Holdings signs deal", "Visit Barzan Towers", "Barzan Gas output", "Inside Barzan",
                    "Why Barzan matters", "New solar farm", "Big solar push", "Rooftop solar grows",
                    "Cheap solar panels", "Qatar solar output rises"]


class Phrase(unittest.TestCase):
    def test_the_name_without_its_legal_form(self):
        self.assertEqual(phrase("Qatar Steel Industries Factory W.L.L"), ["QATAR", "STEEL", "INDUSTRIES", "FACTORY"])

    def test_one_word_takes_the_whole_name(self):
        self.assertEqual(phrase("Delta Corporation"), ["DELTA", "CORPORATION"])
        self.assertEqual(phrase("Delta"), ["DELTA"])

    def test_the_anchor_is_the_longest_distinctive_word(self):
        self.assertEqual(anchor(phrase("Qatar Steel Industries Factory")), "STEEL")
        self.assertEqual(anchor(["QATAR", "INDUSTRIES"]), "QATAR")

    def test_consecutive_words_with_the_name_rules(self):
        ws = phrase("Qatar Aluminum Extrusion")
        self.assertTrue(mentions(ws, words("qatar aluminium extrusion wins award")))
        self.assertFalse(mentions(ws, words("Qatar and aluminum extrusion makers")))
        self.assertTrue(mentions(phrase("Gulf Plastic"), words("gulf plastics factory expands")))   # a plural


class Generic(unittest.TestCase):
    common = common_words(PENINSULA_TITLES)

    def test_common_words_come_from_lower_case_use(self):
        self.assertIn("BUS", self.common)        # 5 of 5 in lower case
        self.assertIn("SOLAR", self.common)      # 5 in lower case; "Solar plant opens" is not counted
        self.assertNotIn("BARZAN", self.common)  # a name, always capitalised

    def test_short_names_of_common_words_are_generic(self):
        self.assertTrue(is_generic("Doha Bus", self.common))
        self.assertTrue(is_generic("Qatar Solar", self.common))
        self.assertTrue(is_generic("Qatar Industries", self.common))   # nothing distinctive at all
        self.assertFalse(is_generic("Barzan Bus", self.common))
        self.assertFalse(is_generic("Doha Bus Solar Bus", self.common))   # four words: specific as a phrase


class OneCustomer(unittest.TestCase):
    TODAY = date(2026, 11, 4)
    ROWS = [("The Peninsula", "2026-10-30", "Barzan Bus fined for violations", "u1"),
            ("Gulf Times", "2026-10-30", "barzan bus fined for violations", "u2"),       # the same headline
            ("QNA", "2024-01-02", "barzan bus wins contract", "u3"),
            ("QNA", "2024-01-03", "barzanbus fans gather", "u4"),                        # not the phrase
            ] + [("The Peninsula", "2024-01-01", t, f"p{i}") for i, t in enumerate(PENINSULA_TITLES)]

    def setUp(self):
        self.h = Headlines.build(self.ROWS, {anchor(phrase("Barzan Bus")), anchor(phrase("Doha Bus"))})

    def test_found(self):
        row = match_one({"customer_id": "1", "name": "BARZAN BUS W.L.L", "cr_number": "1", "search_name": ""},
                        self.h, (7, ["Barzan Bus fined for violations", "Barzan Bus opens depot"]), self.TODAY)
        self.assertEqual({k: row[k] for k in ("status", "generic", "gt_articles", "headlines", "headlines_365d",
                                              "papers", "days_since_last", "negative")},
                         {"status": "found", "generic": 0, "gt_articles": 7, "headlines": 2, "headlines_365d": 1,
                          "papers": 3, "days_since_last": 5, "negative": 1})
        self.assertTrue(row["examples"].startswith("2026-10-30 "))

    def test_generic_is_all_empty(self):
        row = match_one({"customer_id": "2", "name": "DOHA BUS CO", "cr_number": "2", "search_name": ""},
                        self.h, None, self.TODAY)
        self.assertEqual((row["status"], row["generic"], row["headlines"], row["gt_articles"], row["negative"]),
                         ("generic", 1, None, None, None))

    def test_a_failed_search_is_empty_not_zero(self):
        row = match_one({"customer_id": "3", "name": "Barzan Bus", "cr_number": "3", "search_name": ""},
                        self.h, None, self.TODAY)
        self.assertEqual((row["gt_articles"], row["headlines"]), (None, 2))


if __name__ == "__main__":
    unittest.main()
