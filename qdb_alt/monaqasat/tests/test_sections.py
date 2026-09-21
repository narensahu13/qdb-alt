"""The wider harvest: TenderDetails, the classified register, and the
bid-versus-win analysis that the opened sections make possible."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monaqasat.parse import (BID_KINDS, COMPANY_KINDS, LISTING_KINDS,
                             TENDER_KINDS, kind_path, parse_company_list,
                             parse_listing, parse_tender_details)
from monaqasat.store import Store

FIX = Path(__file__).resolve().parent.parent / "fixtures"
TD = (FIX / "tender_details_659460.html").read_text(encoding="utf-8")
CC = (FIX / "classified_companies_p1.html").read_text(encoding="utf-8")
LISTING = (FIX / "listing_awarded_p1.html").read_text(encoding="utf-8")


class Families(unittest.TestCase):
    def test_seven_states_each_side(self):
        self.assertEqual(len(LISTING_KINDS), 14)
        self.assertEqual(len(TENDER_KINDS), 7)
        self.assertEqual(len(BID_KINDS), 7)

    def test_only_opened_and_awarded_name_companies(self):
        self.assertEqual(set(COMPANY_KINDS),
                         {"technical", "financial", "awarded",
                          "bids-technical", "bids-financial", "bids-awarded"})

    def test_paths_resolve(self):
        self.assertEqual(kind_path("awarded"), "AwardedTenders")
        self.assertEqual(kind_path("bids-awarded"), "AwardedBids")
        self.assertEqual(kind_path("available"), "AvailableMinistriesTenders")

    def test_listing_rows_carry_family_and_state(self):
        rows = parse_listing(LISTING, "awarded")
        self.assertEqual(rows[0]["family"], "tender")
        self.assertEqual(rows[0]["state"], "awarded")

    def test_unknown_kind_leaves_them_unset(self):
        rows = parse_listing(LISTING)
        self.assertIsNone(rows[0]["family"])


class TenderDetails(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_tender_details(TD, "659460")
        self.t = self.parsed["tender"]

    def test_header_table(self):
        self.assertEqual(self.t["tender_number"], "3206/2026")
        self.assertEqual(self.t["ministry"], "Ministry of Municipality")
        self.assertEqual(self.t["entity_tender_number"], "56")
        self.assertEqual(self.t["closing_date"], "2026-07-12")
        self.assertEqual(self.t["tender_bond"], 80000.0)

    def test_terms_table(self):
        self.assertEqual(self.t["targeted_tenderer"], "Companies")
        self.assertEqual(self.t["service_delivery"], "Fully Compliant")
        self.assertEqual(self.t["validity_period"], "90")

    def test_icv_requirement_is_captured(self):
        self.assertEqual(self.t["local_value_system"], "Local Value Certificate")

    def test_brief_description_survives_arabic(self):
        self.assertTrue(self.t["brief_description"])
        self.assertIn("الم", self.t["brief_description"])

    def test_name_value_table(self):
        self.assertEqual(self.t["contract_duration"], "36")
        self.assertEqual(self.t["final_insurance"], "10")
        self.assertEqual(self.t["delivery_location"], "Doha Municipality")
        self.assertEqual(self.t["evaluation_criteria"], "The Ordinary Method")

    def test_blank_cells_are_none(self):
        self.assertIsNone(self.t["warranty_period"])
        self.assertIsNone(self.t["auction_type"])

    def test_required_activity_codes(self):
        acts = self.parsed["activities"]
        self.assertEqual([a["activity_code"] for a in acts],
                         ["960320", "960390"])
        self.assertEqual(acts[0]["activity_name"],
                         "Burial services for the dead")

    def test_empty_page_yields_nothing(self):
        p = parse_tender_details("<html></html>", "X")
        self.assertEqual(p["activities"], [])
        self.assertIsNone(p["tender"].get("ministry"))


class ClassifiedRegister(unittest.TestCase):
    def setUp(self):
        self.rows = parse_company_list(CC)

    def test_every_row_is_read(self):
        self.assertEqual(len(self.rows), 3)

    def test_profile_fields(self):
        r = self.rows[0]
        self.assertEqual(r["profile_number"], "CP-24-000002")
        self.assertEqual(r["size"], "Small")
        self.assertEqual(r["evaluation"], "Excellent")
        self.assertEqual(r["cr_number"], "72832")
        self.assertEqual(r["cr_secondary"], "110090")

    def test_certificate_expiry_is_parsed(self):
        self.assertEqual(self.rows[0]["certificate_end"], "2027-03-13")
        self.assertEqual(self.rows[2]["certificate_end"], "2026-11-20")

    def test_classification_type_is_kept(self):
        self.assertEqual(self.rows[0]["classification"],
                         "Supplier and Service Provider Classification")
        self.assertEqual(self.rows[2]["classification"],
                         "Contractor Classification")

    def test_activities_come_from_the_hidden_modal(self):
        acts = self.rows[0]["activities"]
        self.assertEqual(len(acts), 3)
        self.assertEqual(acts[0]["activity_code"], "432122")
        self.assertEqual(acts[0]["grade"], "Seventh")

    def test_each_row_gets_its_own_modal(self):
        self.assertEqual(len(self.rows[1]["activities"]), 2)
        self.assertEqual(len(self.rows[2]["activities"]), 1)
        self.assertEqual(self.rows[1]["activities"][0]["activity_code"],
                         "46900")

    def test_branch_suffix_is_preserved_in_cr(self):
        self.assertEqual(self.rows[1]["cr_number"], "29309/1")

    def test_empty_page_is_empty(self):
        self.assertEqual(parse_company_list("<html></html>"), [])


class RegisterStorage(unittest.TestCase):
    def _store(self, tmp):
        store = Store(str(Path(tmp) / "t.db"))
        for r in parse_company_list(CC):
            store.upsert_company(r, page=1)
        store.commit()
        return store

    def test_companies_and_activities_land(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            s = store.stats()
            self.assertEqual(s["classified companies"], 3)
            self.assertEqual(s["company activities"], 6)
            store.close()

    def test_cr_root_is_indexed_for_joining(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            row = store.conn.execute(
                "SELECT name, size FROM classified_company WHERE cr_root=?",
                ("29309",)).fetchone()
            self.assertEqual(row["name"], "NAJM ALFARID TRADING")
            self.assertEqual(row["size"], "Medium")
            store.close()

    def test_recrawl_replaces_rather_than_duplicates(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for r in parse_company_list(CC):
                store.upsert_company(r, page=1)
            store.commit()
            self.assertEqual(store.stats()["classified companies"], 3)
            self.assertEqual(store.stats()["company activities"], 6)
            store.close()


class BidVersusWin(unittest.TestCase):
    """The point of crawling the opened sections: a borrower that bids and
    never wins looks nothing like one that never bids."""

    def _store(self, tmp):
        store = Store(str(Path(tmp) / "t.db"))
        # won one
        store.upsert_tender({"tender_id": "1", "family": "tender",
                             "state": "awarded", "awarded_date": "2026-01-01"})
        store.replace_companies("1", [
            {"role": "awarded", "seq": 0, "name": "WINNER CO",
             "name_normalised": "winner", "cr_number": "111", "value": 1000.0}])
        # bid twice, won nothing
        for tid, state in (("2", "technical"), ("3", "financial")):
            store.upsert_tender({"tender_id": tid, "family": "tender",
                                 "state": state, "awarded_date": "2026-02-01"})
            store.replace_companies(tid, [
                {"role": "bidder", "seq": 0, "name": "LOSER CO",
                 "name_normalised": "loser", "cr_number": "222",
                 "value": 900.0}])
        store.commit()
        return store

    def _rates(self, store):
        rows = store.conn.execute(
            "SELECT COALESCE(c.cr_root, c.name_normalised) AS key,"
            " MIN(c.name) AS name,"
            " COUNT(DISTINCT CASE WHEN c.role='awarded' THEN c.tender_id END) won,"
            " COUNT(DISTINCT c.tender_id) appearances"
            " FROM company c JOIN tender t USING (tender_id) GROUP BY key"
        ).fetchall()
        return {r["name"]: (r["appearances"], r["won"]) for r in rows}

    def test_a_bidder_that_never_wins_is_visible(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            rates = self._rates(store)
            self.assertEqual(rates["LOSER CO"], (2, 0))
            store.close()

    def test_a_winner_is_counted_once(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertEqual(self._rates(store)["WINNER CO"], (1, 1))
            store.close()

    def test_state_breakdown_separates_the_families(self):
        with TemporaryDirectory() as tmp:
            store = self._store(tmp)
            states = {r["state"]: r["n"] for r in store.by_state()}
            self.assertEqual(states,
                             {"awarded": 1, "technical": 1, "financial": 1})
            store.close()


if __name__ == "__main__":
    unittest.main()
