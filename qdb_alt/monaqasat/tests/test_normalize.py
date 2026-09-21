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
        self.assertEqual(consonant_skeleton("كروي"),
                         consonant_skeleton("Kyrwy"))

    def test_skeleton_ignores_vowels_and_al(self):
        self.assertEqual(consonant_skeleton("AL RAYYAN TRADING"),
                         consonant_skeleton(arabic_to_latin("شركة الريان للتجارة")))


if __name__ == "__main__":
    unittest.main()
