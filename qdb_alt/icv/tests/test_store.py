"""The store, and the only question it really has to answer:

    what was this company's score, and its standing, on that date?

A score read as at today and used against a default from two years ago is
look-ahead bias, and it flatters every model it touches. These tests are what
stop that.
"""

from __future__ import annotations

import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from icv import cli
    from icv.store import PACK_SKIP, Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv import cli
    from icv.store import PACK_SKIP, Store

GUID = "aaaaaaaa-0000-0000-0000-000000000000"
OTHER = "bbbbbbbb-0000-0000-0000-000000000000"


def listing(guid=GUID, cr="185319", name="EPO POWER AND CONTROL TRADING",
            score=18.97, status="Valid", code=100000002, cert="10011086",
            expiry="2027-12-23", industry="Engineering and Construction"):
    return {"id": guid, "cr_number": cr, "cr_root": cr, "name": name,
            "name_norm": name.lower(), "industry": industry,
            "total_score": score, "status": status, "status_code": code,
            "certificate_no": cert, "expiry_date": expiry,
            "manufacturer": None}


def observation(oid, on, score, reason="Certificate Renewed", change=0.0):
    return {"id": oid, "posted_at": on + "T09:00:00Z", "posted_date": on,
            "score": score, "reason": reason, "change": change,
            "reason_code": 1}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "icv.db")
        self.s = Store(self.path)
        self.addCleanup(self.s.close)


class PointInTime(Base):
    def setUp(self):
        super().setUp()
        self.s.see_company(listing(), "2026-01-10")
        self.s.save_history(GUID, [
            observation("o1", "2024-03-28", 10.84, "New Certificate Issued", 10.84),
            observation("o2", "2024-11-23", 17.85,
                        "Transitional Percentage Added", 7.00),
            observation("o3", "2025-06-26", 18.91, "Certificate Renewed", 1.06),
        ], "2026-01-10")

    def test_the_score_on_a_date_is_the_one_published_by_then(self):
        self.assertEqual(self.s.score_as_at(GUID, "2024-06-01")["score"], 10.84)
        self.assertEqual(self.s.score_as_at(GUID, "2024-11-23")["score"], 17.85)
        self.assertEqual(self.s.score_as_at(GUID, "2025-01-01")["score"], 17.85)
        self.assertEqual(self.s.score_as_at(GUID, "2026-09-24")["score"], 18.91)

    def test_before_the_first_certificate_there_is_no_score(self):
        # Not zero, and not today's score: the company was not certified.
        self.assertIsNone(self.s.score_as_at(GUID, "2024-01-01"))

    def test_the_reason_travels_with_the_score(self):
        row = self.s.score_as_at(GUID, "2025-01-01")
        self.assertEqual(row["reason"], "Transitional Percentage Added")
        self.assertEqual(row["change"], 7.00)

    def test_reading_it_twice_adds_nothing(self):
        again = self.s.save_history(GUID, [
            observation("o1", "2024-03-28", 10.84, "New Certificate Issued", 10.84),
            observation("o2", "2024-11-23", 17.85,
                        "Transitional Percentage Added", 7.00),
            observation("o3", "2025-06-26", 18.91, "Certificate Renewed", 1.06),
            observation("o4", "2026-09-23", 18.97, "Certificate Renewed", -0.16),
        ], "2026-02-10")
        self.assertEqual(again, 1)
        self.assertEqual(self.s.stats()["score observations"], 4)


class ListingPeriods(Base):
    def test_a_change_closes_one_period_and_opens_the_next(self):
        self.s.see_company(listing(), "2026-01-10")
        self.s.see_company(listing(status="Grace Period", code=100000005),
                           "2026-02-10")
        rows = self.s.conn.execute(
            "SELECT status, from_date, to_date FROM listing_state"
            " WHERE company_id = ? ORDER BY from_date", (GUID,)).fetchall()
        self.assertEqual([r["status"] for r in rows],
                         ["Valid", "Grace Period"])
        self.assertEqual(rows[0]["to_date"], "2026-02-10")
        self.assertIsNone(rows[1]["to_date"])

    def test_standing_can_be_read_as_at_a_past_date(self):
        self.s.see_company(listing(), "2026-01-10")
        self.s.see_company(listing(status="Grace Period", code=100000005),
                           "2026-02-10")
        self.assertEqual(self.s.status_as_at(GUID, "2026-01-20")["status"],
                         "Valid")
        self.assertEqual(self.s.status_as_at(GUID, "2026-03-01")["status"],
                         "Grace Period")

    def test_an_unchanged_sweep_does_not_start_a_new_period(self):
        self.s.see_company(listing(), "2026-01-10")
        self.assertEqual(self.s.see_company(listing(), "2026-02-10"), "same")
        self.assertEqual(self.s.conn.execute(
            "SELECT COUNT(*) FROM listing_state").fetchone()[0], 1)

    def test_two_sweeps_in_one_day_do_not_leave_a_zero_length_period(self):
        self.s.see_company(listing(), "2026-01-10")
        self.s.see_company(listing(score=20.0), "2026-01-10")
        rows = self.s.conn.execute(
            "SELECT total_score, from_date, to_date FROM listing_state"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_score"], 20.0)
        self.assertIsNone(rows[0]["to_date"])

    def test_a_score_move_is_reported_as_changed(self):
        self.s.see_company(listing(), "2026-01-10")
        self.assertEqual(self.s.see_company(listing(score=22.5), "2026-02-10"),
                         "changed")


class Cohort(Base):
    def test_a_borrower_can_be_read_against_its_industry_on_a_date(self):
        for i, (guid, score) in enumerate([(GUID, 18.91), (OTHER, 60.0)]):
            self.s.see_company(listing(guid=guid, cr=str(100 + i),
                                       industry="Manufacturing"), "2026-01-10")
            self.s.save_history(guid, [observation(f"x{i}", "2025-01-01", score)],
                                "2026-01-10")
        scores = self.s.industry_scores_as_at("Manufacturing", "2026-01-01")
        self.assertEqual(sorted(scores), [18.91, 60.0])
        self.assertEqual(
            self.s.industry_scores_as_at("Manufacturing", "2024-01-01"), [])


class PackedCopy(Base):
    def test_a_shared_copy_carries_no_customer_and_no_stored_pages(self):
        self.s.see_company(listing(), "2026-01-10")
        self.s.save_history(GUID, [observation("o1", "2025-01-01", 18.91)],
                            "2026-01-10")
        self.s.save_raw("/x", "listing", {"Records": []})
        self.s.save_matches([{"customer_id": "CIF-SECRET-42",
                              "company_id": GUID, "method": "cr",
                              "matched_on": "185319", "confidence": 1.0}],
                            None)
        self.s.conn.commit()
        out = str(Path(self.tmp.name) / "slim.db")
        with redirect_stdout(io.StringIO()):
            cli.cmd_pack(types.SimpleNamespace(db=self.path, out=out))
        blob = Path(out).read_bytes()
        self.assertNotIn(b"CIF-SECRET-42", blob)
        copied = Store(out)
        try:
            self.assertEqual(copied.stats()["score observations"], 1)
            self.assertEqual(copied.stats()["companies"], 1)
            for table in PACK_SKIP:
                n = copied.conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertEqual(n, 0, f"{table} must not be copied")
        finally:
            copied.close()


if __name__ == "__main__":
    unittest.main()
