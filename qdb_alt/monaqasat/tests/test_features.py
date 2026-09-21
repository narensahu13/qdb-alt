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


if __name__ == "__main__":
    unittest.main()
