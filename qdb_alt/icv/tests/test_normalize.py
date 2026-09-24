"""CR numbers, in every form they turn up in.

The name handling is inherited from the Monaqasat package and tested there.
What is new here is the QFC form, so that is what this covers, together with
the forms a customer book arrives in after a trip through Excel.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from icv.normalize import cr_root, normalize_name
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv.normalize import cr_root, normalize_name


class CommercialRegistration(unittest.TestCase):
    def test_the_forms_a_customer_book_arrives_in(self):
        for raw, want in [
            ("185319", "185319"),
            ("029309", "29309"),          # leading zeros
            ("29,309", "29309"),          # Excel thousands separator
            (29309.0, "29309"),           # Excel number as a float
            ("29309/4", "29309"),         # branch suffix
            ("CR-29309", "29309"),        # prefixed
            ("٢٩٣٠٩", "29309"),           # Arabic-Indic digits
            ("  185319  ", "185319"),
        ]:
            self.assertEqual(cr_root(raw), want, raw)

    def test_the_qfc_form_the_icv_register_uses(self):
        for raw in ("QFC/00326", "QFC00326", "qfc-326", "QFC 326"):
            self.assertEqual(cr_root(raw), "QFC326", raw)

    def test_a_qfc_number_never_collapses_onto_a_mainland_one(self):
        # This is the whole point. Qatar Financial Centre registrations are
        # numbered separately, so stripping the prefix would join a borrower
        # to an unrelated company with the same digits.
        self.assertNotEqual(cr_root("QFC/00326"), cr_root("326"))
        self.assertNotEqual(cr_root("QFC/00326"), cr_root("00326"))

    def test_nothing_usable_is_nothing(self):
        for raw in (None, "", "   ", "QFC/", "n/a", float("nan")):
            self.assertIsNone(cr_root(raw), repr(raw))


class Names(unittest.TestCase):
    def test_the_register_and_the_book_spell_it_differently(self):
        self.assertEqual(normalize_name("EPO POWER AND CONTROL TRADING"),
                         normalize_name("Epo Power & Control Trading"))
        self.assertEqual(normalize_name("ISHAM SERVICES W.L.L"),
                         normalize_name("Isham Services WLL"))


if __name__ == "__main__":
    unittest.main()
