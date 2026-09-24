"""Joining QDB's customer book to the ICV register.

The join is mostly the CR number, which is why this source is worth having:
QDB already holds a CR for every borrower, so it is a join and not a guess.
Two things are specific to ICV and are what these tests are for -- the QFC
registration form, and a borrower that holds more than one registration.
"""

from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from icv.match import load_customers_report, match_suppliers, summarise
    from icv.normalize import cr_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv.match import load_customers_report, match_suppliers, summarise
    from icv.normalize import cr_root


def supplier(guid, name, cr):
    return {"id": guid, "name": name, "cr_number": cr, "cr_root": cr_root(cr)}


def customer(cid, name, cr=None):
    return {"customer_id": cid, "name": name, "cr_number": cr}


REGISTER = [
    supplier("g1", "PARENT CO", "29309"),
    supplier("g2", "THE SUBSIDIARY", "55555"),
    supplier("g3", "SOMEONE ELSE", "77777"),
    supplier("g4", "KAAR TECHNOLOGIES QFC LLC", "QFC/00326"),
    supplier("g5", "AN UNRELATED MAINLAND FIRM", "326"),
]


class QfcRegistrations(unittest.TestCase):
    """QFC numbers run in their own sequence. QFC/00326 and CR 326 are two
    different companies, and joining them would put a stranger's ICV score on
    a borrower's file."""

    def test_a_qfc_customer_reaches_the_qfc_supplier(self):
        m = match_suppliers([customer("CIF-9", "Kaar", cr="QFC/00326")],
                            REGISTER)
        self.assertEqual([x["company_id"] for x in m], ["g4"])
        self.assertEqual(m[0]["method"], "cr")

    def test_a_mainland_customer_does_not_reach_the_qfc_supplier(self):
        m = match_suppliers([customer("CIF-8", "Mainland", cr="326")],
                            REGISTER)
        self.assertEqual([x["company_id"] for x in m], ["g5"])

    def test_the_two_keys_are_not_equal(self):
        self.assertNotEqual(cr_root("QFC/00326"), cr_root("326"))
        self.assertEqual(cr_root("QFC/00326"), cr_root("qfc-326"))


class OneCustomerSeveralRegistrations(unittest.TestCase):
    def test_every_row_of_a_customer_is_matched(self):
        m = match_suppliers([customer("CIF-1", "Parent", cr="29309"),
                             customer("CIF-1", "Subsidiary", cr="55555")],
                            REGISTER)
        self.assertEqual({x["company_id"] for x in m}, {"g1", "g2"})
        self.assertEqual({x["customer_id"] for x in m}, {"CIF-1"})

    def test_a_branch_number_still_reaches_the_parent(self):
        m = match_suppliers([customer("CIF-1", "Parent", cr="29309/4")],
                            REGISTER)
        self.assertEqual([x["matched_name"] for x in m], ["PARENT CO"])

    def test_two_rows_on_one_supplier_keep_the_stronger_evidence(self):
        m = match_suppliers([customer("CIF-2", "x", cr="29309"),
                             customer("CIF-2", "PARENT CO")], REGISTER)
        self.assertEqual([(x["company_id"], x["method"]) for x in m],
                         [("g1", "cr")])

    def test_the_summary_counts_customers_not_rows(self):
        m = match_suppliers([customer("CIF-1", "Parent", cr="29309"),
                             customer("CIF-1", "Subsidiary", cr="55555")],
                            REGISTER)
        s = summarise(m)
        self.assertEqual(s["matches"], 2)
        self.assertEqual(s["customers matched"], 1)
        self.assertEqual(s["suppliers matched"], 2)


class WithoutACrNumber(unittest.TestCase):
    def test_an_exact_name_still_joins(self):
        m = match_suppliers([customer("CIF-3", "Parent Co")], REGISTER)
        self.assertEqual([x["company_id"] for x in m], ["g1"])
        self.assertEqual(m[0]["method"], "name_exact")

    def test_a_name_that_matches_nothing_matches_nothing(self):
        m = match_suppliers([customer("CIF-4", "A COMPANY THAT IS NOT THERE")],
                            REGISTER)
        self.assertEqual(m, [])


class LoadingTheBook(unittest.TestCase):
    def test_repeated_ids_are_a_note_and_not_a_warning(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "book.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["customer_id", "name", "cr_number"])
                w.writerow(["CIF-1", "PARENT CO", "29309"])
                w.writerow(["CIF-1", "THE SUBSIDIARY", "55555"])
            rows, report = load_customers_report(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(report["warnings"], [])
        self.assertTrue(any("share a customer id" in n
                            for n in report["notes"]), report["notes"])


if __name__ == "__main__":
    unittest.main()
