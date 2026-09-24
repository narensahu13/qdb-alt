"""What the ICV mirror hands a model, and when it refuses to.

A score on its own tells you almost nothing. These tests are about the three
things that make it usable: it has to be the score the company held on the
observation date, the part of a move the company earned has to be separable
from the part the rules handed everyone, and a borrower has to be readable
against its own sector rather than a fixed number.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from icv.features import (POLICY_REASONS, build_client_months,
                              client_rows, cohort_medians, score_profile,
                              standing)
    from icv.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv.features import (POLICY_REASONS, build_client_months,
                              client_rows, cohort_medians, score_profile,
                              standing)
    from icv.store import Store

A = "aaaaaaaa-0000-0000-0000-000000000000"   # the borrower
B = "bbbbbbbb-0000-0000-0000-000000000000"   # a peer in the same industry
C = "cccccccc-0000-0000-0000-000000000000"   # a different industry


def obs(on, score, reason="Certificate Renewed", change=0.0):
    return {"date": date.fromisoformat(on), "score": score,
            "reason": reason, "change": change}


# The real shape of a supplier's series: a first certificate, a small
# genuine improvement, then the transitional percentage handed to everyone
# in November 2024, then another renewal.
SERIES = [
    obs("2024-03-28", 10.84, "New Certificate Issued", 10.84),
    obs("2024-08-18", 10.85, "Sub Supplier Score Updated", 0.01),
    obs("2024-11-23", 17.85, "Transitional Percentage Added  ", 7.00),
    obs("2025-06-26", 18.91, "Certificate Renewed", 1.06),
]


class TheScoreOnADate(unittest.TestCase):
    def test_it_is_the_one_published_by_then(self):
        self.assertEqual(score_profile(SERIES, date(2024, 6, 1))["icv_score"],
                         10.84)
        self.assertEqual(score_profile(SERIES, date(2025, 1, 1))["icv_score"],
                         17.85)
        self.assertEqual(score_profile(SERIES, date(2026, 9, 24))["icv_score"],
                         18.91)

    def test_before_the_first_certificate_there_is_no_score(self):
        # Not zero. A company that held no certificate is not a company
        # with a bad one, and a model must be able to tell them apart.
        p = score_profile(SERIES, date(2024, 1, 1))
        self.assertIsNone(p["icv_score"])
        self.assertIsNone(p["icv_score_change_12m"])
        self.assertEqual(p["icv_observations"], 0)

    def test_the_trajectory_comes_with_it(self):
        p = score_profile(SERIES, date(2025, 6, 30))
        self.assertEqual(p["icv_score"], 18.91)
        self.assertEqual(p["icv_score_12m_ago"], 10.84)
        self.assertEqual(p["icv_score_change_12m"], 8.07)
        self.assertEqual(p["icv_first_certificate"], "2024-03-28")
        self.assertEqual(p["icv_months_certified"], 15)
        self.assertEqual(p["icv_months_since_last_change"], 0)


class PolicyIsNotPerformance(unittest.TestCase):
    """+7.00 in November 2024 was a rule change applied across the register.
    A model fed raw deltas would learn that date, not a borrower."""

    def test_the_transitional_percentage_is_named_as_policy(self):
        self.assertIn("Transitional Percentage Added", POLICY_REASONS)

    def test_the_move_is_split_into_earned_and_granted(self):
        p = score_profile(SERIES, date(2025, 6, 30))
        self.assertEqual(p["icv_policy_change_12m"], 7.00)
        self.assertEqual(p["icv_organic_change_12m"], 1.07)   # 0.01 + 1.06
        self.assertEqual(round(p["icv_policy_change_12m"]
                               + p["icv_organic_change_12m"], 2),
                         p["icv_score_change_12m"])

    def test_a_year_with_no_policy_change_says_so(self):
        p = score_profile(SERIES, date(2026, 1, 31))
        self.assertIsNone(p["icv_policy_change_12m"])
        self.assertEqual(p["icv_organic_change_12m"], 1.06)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = Store(str(Path(self.tmp.name) / "icv.db"))
        self.addCleanup(self.s.close)
        for guid, cr, name, industry in (
                (A, "185319", "BORROWER TRADING", "Manufacturing"),
                (B, "222222", "A PEER", "Manufacturing"),
                (C, "333333", "SOMEONE IN IT", "Information Technology")):
            self.s.see_company({"id": guid, "cr_number": cr, "cr_root": cr,
                                "name": name, "name_norm": name.lower(),
                                "industry": industry, "total_score": 18.91,
                                "status": "Valid", "status_code": 100000002,
                                "certificate_no": "10011086",
                                "expiry_date": "2027-12-23",
                                "manufacturer": None}, "2026-09-01")
        for i, o in enumerate(SERIES):
            self.s.save_history(A, [{
                "id": f"a{i}", "posted_at": o["date"].isoformat() + "T09:00:00Z",
                "posted_date": o["date"].isoformat(), "score": o["score"],
                "change": o["change"], "reason": o["reason"].strip(),
                "reason_code": 1}], "2026-09-01")
        self.s.save_history(B, [{
            "id": "b0", "posted_at": "2024-01-01T09:00:00Z",
            "posted_date": "2024-01-01", "score": 40.0, "change": 40.0,
            "reason": "New Certificate Issued", "reason_code": 1}],
            "2026-09-01")
        self.s.save_history(C, [{
            "id": "c0", "posted_at": "2024-01-01T09:00:00Z",
            "posted_date": "2024-01-01", "score": 80.0, "change": 80.0,
            "reason": "New Certificate Issued", "reason_code": 1}],
            "2026-09-01")
        self.s.save_matches([{"customer_id": "CIF-1", "company_id": A,
                              "method": "cr", "matched_on": "185319",
                              "confidence": 1.0}], None)


class Cohort(Base):
    def test_the_median_is_its_own_sector_at_the_same_date(self):
        med = cohort_medians(self.s, date(2025, 6, 30))
        self.assertEqual(med["Manufacturing"], round((18.91 + 40.0) / 2, 2))
        self.assertEqual(med["Information Technology"], 80.0)

    def test_the_sector_moves_too_so_the_date_matters(self):
        # In mid-2024 the borrower was on 10.84, not 18.91.
        med = cohort_medians(self.s, date(2024, 6, 30))
        self.assertEqual(med["Manufacturing"], round((10.84 + 40.0) / 2, 2))

    def test_a_borrower_is_placed_against_it(self):
        row = [r for r in client_rows(self.s, date(2025, 6, 30))
               if r["client_id"] == "CIF-1"][0]
        median = cohort_medians(self.s, date(2025, 6, 30))["Manufacturing"]
        self.assertEqual(row["icv_score"], 18.91)
        self.assertEqual(row["icv_industry_median"], median)
        self.assertEqual(row["icv_vs_industry"], round(18.91 - median, 2))
        self.assertLess(row["icv_vs_industry"], 0)   # below its own sector


class Standing(Base):
    def test_before_the_first_sweep_the_standing_is_unknown(self):
        # The portal publishes the score history but not the status
        # history. Saying "Valid in 2024" because it is valid now would be
        # inventing the one thing this data cannot tell us.
        st = standing(self.s, A, date(2024, 6, 30))
        self.assertIsNone(st["icv_status"])
        self.assertEqual(st["icv_standing_known"], 0)

    def test_from_the_first_sweep_onwards_it_is_known(self):
        st = standing(self.s, A, date(2026, 9, 24))
        self.assertEqual(st["icv_status"], "Valid")
        self.assertEqual(st["icv_standing_known"], 1)
        self.assertEqual(st["icv_expiry_date"], "2027-12-23")
        self.assertGreater(st["icv_days_to_expiry"], 0)


class ClientMonths(Base):
    def setUp(self):
        super().setUp()
        build_client_months(self.s, as_of_end=date(2026, 9, 30))
        self.months = {r["as_of_month"]: r for r in self.s.conn.execute(
            "SELECT * FROM client_month WHERE client_id='CIF-1'")}

    def test_no_row_before_the_company_was_certified(self):
        self.assertNotIn("2024-01-31", self.months)
        self.assertIn("2024-03-31", self.months)

    def test_each_month_holds_the_score_of_that_month(self):
        self.assertEqual(self.months["2024-06-30"]["score"], 10.84)
        self.assertEqual(self.months["2025-01-31"]["score"], 17.85)
        self.assertEqual(self.months["2026-09-30"]["score"], 18.91)

    def test_the_policy_jump_is_visible_and_separate(self):
        row = self.months["2024-11-30"]
        self.assertEqual(row["policy_change_12m"], 7.00)
        self.assertEqual(row["organic_change_12m"], 10.85)   # 10.84 + 0.01
        row = self.months["2026-09-30"]
        self.assertIsNone(row["policy_change_12m"])

    def test_the_standing_columns_are_empty_for_the_past(self):
        self.assertEqual(self.months["2024-06-30"]["standing_known"], 0)
        self.assertEqual(self.months["2026-09-30"]["standing_known"], 1)

    def test_rebuilding_does_not_stack_rows(self):
        before = self.s.conn.execute(
            "SELECT COUNT(*) FROM client_month").fetchone()[0]
        build_client_months(self.s, as_of_end=date(2026, 9, 30))
        after = self.s.conn.execute(
            "SELECT COUNT(*) FROM client_month").fetchone()[0]
        self.assertEqual(before, after)

    def test_a_packed_copy_still_carries_no_client(self):
        from icv.store import PACK_SKIP
        self.assertIn("client_month", PACK_SKIP)


if __name__ == "__main__":
    unittest.main()
