"""Arabic transliteration used when the book and the site disagree."""

import unittest

from monaqasat.normalize import (arabic_to_latin, consonant_skeleton,
                                 is_arabic, match_keys, similarity)


class Arabic(unittest.TestCase):
    def test_detects_arabic(self):
        self.assertTrue(is_arabic("شركة الريان"))
        self.assertFalse(is_arabic("AL RAYYAN TRADING"))

    def test_rayyan_collides_with_latin(self):
        latin = arabic_to_latin("شركة الريان للتجارة")
        self.assertIn("trading", latin)
        keys = match_keys("شركة الريان للتجارة") | match_keys("AL RAYYAN TRADING WLL")
        # at least one shared comparison key, or a high similarity
        ar = match_keys("شركة الريان للتجارة")
        en = match_keys("AL RAYYAN TRADING WLL")
        self.assertTrue(ar & en or similarity(
            "شركة الريان للتجارة", "AL RAYYAN TRADING WLL") >= 0.9, (ar, en))

    def test_kyrwy_stem(self):
        # "Kyrwy" is the site's letter-by-letter rendering of كيروي
        # (k-y-r-w-y). The earlier test used كروي, which is a different
        # four-letter word; it only passed because y was dropped from
        # skeletons, which is what made "Nasser" equal "Nisr".
        self.assertEqual(consonant_skeleton("كيروي"),
                         consonant_skeleton("Kyrwy"))

    def test_skeleton_ignores_vowels_and_al(self):
        self.assertEqual(consonant_skeleton("AL RAYYAN TRADING"),
                         consonant_skeleton(arabic_to_latin("شركة الريان للتجارة")))



class CrNumbers(unittest.TestCase):
    def test_every_format_a_cr_turns_up_in(self):
        from monaqasat.normalize import cr_root
        for raw in ("29309", "29309/1", "CR-29309", "CR 29309", "029309",
                    "29,309", "29309.0", 29309.0, "٢٩٣٠٩", "۲۹۳۰۹", " 29309 "):
            self.assertEqual(cr_root(raw), "29309", repr(raw))

    def test_nothing_is_not_a_number(self):
        from monaqasat.normalize import cr_root
        for raw in (None, "", "N/A", "0", float("nan")):
            self.assertIsNone(cr_root(raw), repr(raw))

    def test_a_thousands_separator_is_not_a_short_cr(self):
        from monaqasat.normalize import cr_root
        self.assertEqual(cr_root("29,309"), "29309")      # was "29"
        self.assertEqual(cr_root("29309, 29310"), "29309")


class ArabicScript(unittest.TestCase):
    def test_arabic_names_have_a_key(self):
        from monaqasat.normalize import normalize_name
        # used to normalise to '' -- every Arabic name was the same company
        self.assertEqual(normalize_name("قطر للوقود (وقود)"), "قطر وقود وقود")

    def test_spelling_variants_fold_together(self):
        from monaqasat.normalize import normalize_name
        variants = ["شركة الريان للتجارة ذ.م.م", "مؤسسة الرّيان للتجارة",
                    "الريان للتجاره"]
        self.assertEqual({normalize_name(v) for v in variants}, {"ريان تجاره"})

    def test_letter_by_letter_rendering_matches_the_site(self):
        self.assertGreaterEqual(
            similarity("كروي للمقاولات والنقليات والخدمات",
                       "Kyrwy Llmqawlat Walnqlyat Walkhdmlt"), 0.9)

    def test_short_skeletons_never_decide_a_match(self):
        # رنا (Rana) and الريان (Rayyan) share 'rn' once y is dropped
        self.assertEqual(similarity("رنا", "RAYYAN"), 0.0)

    def test_same_consonants_are_not_the_same_company(self):
        self.assertLess(similarity("Nasser Trading", "Nisr Trading"), 0.92)
        self.assertLess(similarity("Doha Trading", "Duha Trading"), 0.92)
        self.assertLess(similarity("Qatar Packaging", "Qatar Navigation"), 0.9)


if __name__ == "__main__":
    unittest.main()
