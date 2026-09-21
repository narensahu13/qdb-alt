"""Matching and the end-to-end path, using the store but never the network."""

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monaqasat.match import load_customers, match_customers, summarise
from monaqasat.parse import parse_detail, parse_listing
from monaqasat.store import Store

FIX = Path(__file__).resolve().parent.parent / "fixtures"
DETAIL = (FIX / "detail_659460.html").read_text(encoding="utf-8")
LISTING = (FIX / "listing_awarded_p1.html").read_text(encoding="utf-8")


def _loaded_store(path):
    """Everything `crawl` would do, minus the HTTP."""
    store = Store(path)
    url_l = "https://x/TendersOnlineServices/AwardedTenders/1"
    store.save_page(url_l, "listing", LISTING, page=1)
    for row in parse_listing(LISTING):
        store.upsert_tender(row, source_url=url_l)
    url_d = "https://x/TendersOnlineServices/TenderCompaniesDetails/659460"
    store.save_page(url_d, "detail", DETAIL, tender_id="659460")
    parsed = parse_detail(DETAIL, "659460")
    store.upsert_tender(parsed["tender"], source_url=url_d)
    store.replace_companies("659460", parsed["companies"])
    store.commit()
    return store


class StoreRoundTrip(unittest.TestCase):
    def test_listing_then_detail_keeps_both_sets_of_fields(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            row = store.conn.execute(
                "SELECT * FROM tender WHERE tender_id='659460'").fetchone()
            # from the listing
            self.assertEqual(row["ministry"], "Ministry of Municipality")
            # from the detail page only
            self.assertEqual(row["entity_tender_number"], "56")
            self.assertEqual(row["awarded_amount"], 1819663.33)
            store.close()

    def test_detail_does_not_null_out_listing_fields(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            # Re-apply a detail parse that has no ministry at all.
            thin = parse_detail("<html></html>", "659460")["tender"]
            store.upsert_tender(thin)
            store.commit()
            row = store.conn.execute(
                "SELECT ministry FROM tender WHERE tender_id='659460'"
            ).fetchone()
            self.assertEqual(row["ministry"], "Ministry of Municipality")
            store.close()

    def test_stats_count_what_was_loaded(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            s = store.stats()
            self.assertEqual(s["tenders"], 3)
            self.assertEqual(s["award rows"], 2)
            self.assertEqual(s["bidder rows"], 2)
            self.assertEqual(s["companies with CR"], 4)
            store.close()


class Matching(unittest.TestCase):
    def _companies(self, tmp):
        store = _loaded_store(str(Path(tmp) / "t.db"))
        rows = store.companies()
        store.close()
        return rows

    def test_cr_number_beats_everything(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "Something Else Entirely",
                  "cr_number": "29309"}], companies)
            self.assertTrue(m)
            self.assertEqual(m[0]["method"], "cr")
            self.assertEqual(m[0]["matched_name"], "NAJM ALFARID TRADING")

    def test_cr_branch_suffix_still_joins(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309/4"}],
                companies)
            self.assertEqual({x["method"] for x in m}, {"cr"})

    def test_exact_name_matches_without_a_cr(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C2", "name": "Najm Alfarid Trading WLL",
                  "cr_number": None}], companies)
            self.assertTrue(m)
            self.assertIn(m[0]["method"], {"name_exact", "name_fuzzy"})

    def test_unrelated_customer_does_not_match(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C3", "name": "Doha Dental Clinic",
                  "cr_number": "88888"}], companies)
            self.assertEqual(m, [])

    def test_one_row_per_role(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309"}],
                companies)
            self.assertEqual({x["role"] for x in m}, {"awarded", "bidder"})

    def test_fuzzy_can_be_switched_off(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C4", "name": "Najm Al Farid Trading Co",
                  "cr_number": None}], companies, fuzzy_threshold=1.0)
            self.assertEqual([x for x in m if x["method"] == "name_fuzzy"], [])

    def test_summary_splits_by_method_and_role(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309"}],
                companies)
            s = summarise(m)
            self.assertEqual(s["customers matched"], 1)
            self.assertEqual(s["by method"]["cr"], 2)
            self.assertEqual(s["awarded"], 1)
            self.assertEqual(s["bid only"], 1)

    def test_matches_persist_and_replace_cleanly(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309"}],
                store.companies())
            store.save_matches(m)
            store.save_matches(m)          # idempotent on the primary key
            n = store.conn.execute("SELECT COUNT(*) FROM match").fetchone()[0]
            self.assertEqual(n, 2)
            store.clear_matches()
            n = store.conn.execute("SELECT COUNT(*) FROM match").fetchone()[0]
            self.assertEqual(n, 0)
            store.close()


class MultiWinnerTenders(unittest.TestCase):
    """A tender with two winners must not credit either with the other's
    value. This was a real bug: the profile join fanned out across every
    company row on the tender."""

    def _matches(self, tmp):
        store = _loaded_store(str(Path(tmp) / "t.db"))
        customers = [
            {"customer_id": "NAJM", "name": "x", "cr_number": "29309"},
            {"customer_id": "KYRWY", "name": "y", "cr_number": "99457"},
        ]
        m = match_customers(customers, store.companies())
        store.save_matches(m)
        return store, m

    def test_each_customer_matches_only_its_own_row(self):
        with TemporaryDirectory() as tmp:
            store, m = self._matches(tmp)
            najm = [x for x in m
                    if x["customer_id"] == "NAJM" and x["role"] == "awarded"]
            self.assertEqual(len(najm), 1)
            self.assertEqual(najm[0]["matched_name"], "NAJM ALFARID TRADING")
            store.close()

    def test_awarded_value_is_the_customers_own_line(self):
        with TemporaryDirectory() as tmp:
            store, _ = self._matches(tmp)
            rows = store.conn.execute(
                "SELECT m.customer_id, SUM(c.value) v FROM match m"
                " JOIN company c ON c.tender_id=m.tender_id"
                "   AND c.role=m.role AND c.seq=m.seq"
                " WHERE m.role='awarded' GROUP BY m.customer_id").fetchall()
            got = {r["customer_id"]: round(r["v"], 2) for r in rows}
            self.assertEqual(got, {"NAJM": 1481863.33, "KYRWY": 337800.00})
            store.close()

    def test_the_two_customers_sum_to_the_tender_total(self):
        with TemporaryDirectory() as tmp:
            store, _ = self._matches(tmp)
            total = store.conn.execute(
                "SELECT awarded_amount FROM tender WHERE tender_id='659460'"
            ).fetchone()[0]
            per = store.conn.execute(
                "SELECT SUM(c.value) FROM match m JOIN company c"
                " ON c.tender_id=m.tender_id AND c.role=m.role"
                "   AND c.seq=m.seq WHERE m.role='awarded'").fetchone()[0]
            self.assertAlmostEqual(per, total, places=2)
            store.close()


class CustomerCsv(unittest.TestCase):
    def test_reads_id_name_and_cr(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["customer_id", "name", "cr_number"])
                w.writerow(["C001", "AL RAYYAN TRADING W L L", "104772"])
                w.writerow(["", "NAJM ALFARID TRADING", ""])
            rows = load_customers(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["cr_number"], "104772")
        # a missing id falls back to the name, so nothing is silently dropped
        self.assertEqual(rows[1]["customer_id"], "NAJM ALFARID TRADING")


class ArabicNames(unittest.TestCase):
    """QDB's book is often Arabic; Monaqasat English is a machine
    transliteration. Matching has to survive both, with or without a CR."""

    def test_arabic_customer_matches_latin_site_name(self):
        companies = [{
            "tender_id": "1", "role": "awarded", "seq": 1,
            "name": "AL RAYYAN TRADING WLL", "cr_number": None,
            "cr_root": None, "name_ar": None,
        }]
        m = match_customers(
            [{"customer_id": "C1", "name": "شركة الريان للتجارة",
              "cr_number": None, "name_ar": "شركة الريان للتجارة"}],
            companies)
        self.assertTrue(m)
        self.assertEqual(m[0]["matched_name"], "AL RAYYAN TRADING WLL")
        self.assertIn(m[0]["method"], {"name_exact", "name_fuzzy"})

    def test_name_ar_column_used_when_english_name_differs(self):
        companies = [{
            "tender_id": "1", "role": "awarded", "seq": 1,
            "name": "AL RAYYAN TRADING", "cr_number": None,
            "cr_root": None,
        }]
        m = match_customers(
            [{"customer_id": "C1", "name": "Rayyan Co",
              "cr_number": None, "name_ar": "شركة الريان للتجارة"}],
            companies)
        self.assertTrue(m)

    def test_machine_transliteration_kyrwy(self):
        with TemporaryDirectory() as tmp:
            companies = Matching()._companies(tmp)
            m = match_customers(
                [{"customer_id": "C5",
                  "name": "كروي للمقاولات والنقليات والخدمات",
                  "cr_number": None}],
                companies)
            names = {x["matched_name"].strip() for x in m}
            self.assertTrue(
                any("Kyrwy" in n or "KYRWY" in n.upper() for n in names),
                names)

    def test_cr_still_beats_arabic_name(self):
        with TemporaryDirectory() as tmp:
            companies = Matching()._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "شركة النجم",
                  "cr_number": "29309"}],
                companies)
            self.assertTrue(m)
            self.assertEqual({x["method"] for x in m}, {"cr"})


if __name__ == "__main__":
    unittest.main()
