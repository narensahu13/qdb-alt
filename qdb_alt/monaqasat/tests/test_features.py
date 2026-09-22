"""Canonical hashing and CR-month features. Offline."""

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from monaqasat.features import build_features
from monaqasat.fingerprint import canonical_html_sha256
from monaqasat.store import Store


class Fingerprint(unittest.TestCase):
    def test_dynatrace_script_noise_does_not_change_the_hash(self):
        a = "<html><body>tender<script>dt='abc'</script></body></html>"
        b = "<html><body>tender<script>dt='xyz123'</script></body></html>"
        self.assertEqual(canonical_html_sha256(a), canonical_html_sha256(b))

    def test_real_content_change_does(self):
        a = "<html><body><p>Awarded</p></body></html>"
        b = "<html><body><p>Cancelled</p></body></html>"
        self.assertNotEqual(canonical_html_sha256(a), canonical_html_sha256(b))


class RawPageDedup(unittest.TestCase):
    def test_identical_content_is_unchanged(self):
        with TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "t.db"))
            url = "https://x/AwardedTenders/1"
            self.assertEqual(store.save_page(url, "listing", "<p>one</p>"), "new")
            self.assertEqual(
                store.save_page(url, "listing", "<p>one</p><script>x</script>"),
                "unchanged")
            self.assertEqual(store.save_page(url, "listing", "<p>two</p>"), "changed")
            html = store.conn.execute(
                "SELECT html FROM raw_page WHERE url=?", (url,)).fetchone()[0]
            self.assertEqual(html, "<p>two</p>")
            store.close()


class Features(unittest.TestCase):
    def _store(self, tmp):
        store = Store(str(Path(tmp) / "t.db"))
        store.upsert_tender({"tender_id": "1", "family": "tender",
                             "state": "awarded", "awarded_date": "2026-01-15",
                             "ministry": "Ashghal"})
        store.replace_companies("1", [
            {"role": "awarded", "seq": 0, "name": "WINNER",
             "name_normalised": "winner", "cr_number": "111", "value": 1000.0,
             "stage": "awarded"}])
        for tid in ("2", "3"):
            store.upsert_tender({"tender_id": tid, "family": "tender",
                                 "state": "technical",
                                 "awarded_date": "2026-02-01",
                                 "ministry": "Ashghal"})
            store.replace_companies(tid, [
                {"role": "bidder", "seq": 0, "name": "LOSER",
                 "name_normalised": "loser", "cr_number": "222",
                 "value": 900.0, "stage": "technical"}])
        store.upsert_company({
            "profile_number": "CP-1", "name": "WINNER",
            "name_normalised": "winner", "cr_number": "111",
            "size": "Small", "evaluation": "Good",
            "certificate_end": "2027-01-01", "activities": [],
        })
        store.commit()
        return store

    def test_winner_and_nonwinner_rows(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            result = build_features(store, months=6, as_of_end=date(2026, 6, 30))
            self.assertEqual(result["crs"], 2)
            self.assertGreater(result["rows"], 0)
            winner = store.conn.execute(
                "SELECT * FROM feature_cr_month WHERE cr_root='111'"
                " ORDER BY as_of_month DESC LIMIT 1").fetchone()
            loser = store.conn.execute(
                "SELECT * FROM feature_cr_month WHERE cr_root='222'"
                " ORDER BY as_of_month DESC LIMIT 1").fetchone()
            self.assertEqual(winner["awards_12m_count"], 1)
            self.assertEqual(winner["awards_12m_value"], 1000)
            self.assertEqual(winner["classified_size"], "Small")
            self.assertEqual(loser["awards_12m_count"], 0)
            self.assertEqual(loser["bids_12m_count"], 2)
            self.assertIsNone(loser["win_rate_12m"])  # fewer than 3 bids
            store.close()

    def test_future_as_of_does_not_see_later_awards(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            build_features(store, months=6, as_of_end=date(2026, 6, 30))
            jan = store.conn.execute(
                "SELECT awards_12m_count FROM feature_cr_month"
                " WHERE cr_root='111' AND as_of_month='2026-01-31'"
            ).fetchone()
            self.assertEqual(jan["awards_12m_count"], 1)
            # loser's Feb bids must not appear in January
            early = store.conn.execute(
                "SELECT bids_12m_count FROM feature_cr_month"
                " WHERE cr_root='222' AND as_of_month='2026-01-31'"
            ).fetchone()
            self.assertIsNone(early)
            store.close()



class FeatureFixes(unittest.TestCase):
    """Two v1 errors, found in the 22 Sept 2026 review."""

    def _store(self, tmp):
        store = Store(str(Path(tmp) / "t.db"))
        store.upsert_tender({"tender_id": "A", "family": "tender",
                             "state": "awarded", "awarded_date": "2026-03-01",
                             "ministry": "Ashghal"})
        store.replace_companies("A", [
            {"role": "awarded", "seq": 0, "name": "B CO", "cr_number": "500",
             "value": 600000.0, "stage": "awarded"},
            {"role": "awarded", "seq": 1, "name": "B CO", "cr_number": "500",
             "value": 400000.0, "stage": "awarded"},
            {"role": "bidder", "seq": 0, "name": "B CO", "cr_number": "500",
             "value": 1000000.0, "stage": "financial"}])
        for i, t in enumerate(("P1", "P2", "P3")):
            store.upsert_tender({"tender_id": t, "family": "tender",
                                 "state": "technical",
                                 "closing_date": "2026-05-01",
                                 "ministry": "HMC"})
            store.replace_companies(t, [
                {"role": "bidder", "seq": 0, "name": "B CO",
                 "cr_number": "500", "value": None, "stage": "technical"}])
        for t, winner in (("D1", "600"), ("D2", "600"), ("D3", "500")):
            store.upsert_tender({"tender_id": t, "family": "tender",
                                 "state": "awarded",
                                 "awarded_date": "2026-04-01"})
            store.replace_companies(t, [
                {"role": "awarded", "seq": 0, "name": "W", "cr_number": winner,
                 "value": 10.0, "stage": "awarded"},
                {"role": "bidder", "seq": 0, "name": "B CO",
                 "cr_number": "500", "value": 12.0, "stage": "financial"}])
        store.commit()
        build_features(store, months=3, as_of_end=date(2026, 6, 30))
        return store

    def _last(self, store, cr):
        return store.conn.execute(
            "SELECT * FROM feature_cr_month WHERE cr_root=?"
            " ORDER BY as_of_month DESC LIMIT 1", (cr,)).fetchone()

    def test_award_value_is_the_sum_of_the_lines(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            r = self._last(store, "500")
            # 600k + 400k on A, plus 10 on D3 -- not the smallest line
            self.assertEqual(r["awards_12m_value"], 1000010.0)
            self.assertEqual(r["largest_award_12m"], 1000000.0)
            store.close()

    def test_bids_still_under_evaluation_are_not_losses(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            r = self._last(store, "500")
            self.assertEqual(r["bids_12m_count"], 7)     # A, P1-3, D1-3
            self.assertEqual(r["decided_12m_count"], 4)  # A, D1-3
            self.assertEqual(r["wins_12m_count"], 2)     # A, D3
            self.assertEqual(r["win_rate_12m"], 0.5)     # v1 said 2/7
            store.close()


if __name__ == "__main__":
    unittest.main()
