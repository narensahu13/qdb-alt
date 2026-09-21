"""Parser tests against markup captured from the live site, Sept 2026."""

import unittest
from pathlib import Path

from monaqasat.normalize import (canonical_key, cr_root, normalize_cr,
                                 normalize_name, similarity)
from monaqasat.parse import (kind_from_url, last_listing_page, looks_rejected,
                             parse_date, parse_detail, parse_listing,
                             parse_money)

FIX = Path(__file__).resolve().parent.parent / "fixtures"
DETAIL = (FIX / "detail_659460.html").read_text(encoding="utf-8")
LISTING = (FIX / "listing_awarded_p1.html").read_text(encoding="utf-8")


class Scalars(unittest.TestCase):
    def test_date_is_day_first(self):
        self.assertEqual(parse_date("21/06/2026"), "2026-06-21")

    def test_bad_date_is_none_not_an_exception(self):
        self.assertIsNone(parse_date("31/02/2026"))
        self.assertIsNone(parse_date("not a date"))
        self.assertIsNone(parse_date(None))

    def test_money_strips_commas_and_currency(self):
        self.assertEqual(parse_money("1,481,863.33 QAR"), 1481863.33)
        self.assertEqual(parse_money(" 8,000.00 "), 8000.0)
        self.assertIsNone(parse_money(""))


class Detail(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_detail(DETAIL, "659460")
        self.tender = self.parsed["tender"]
        self.companies = self.parsed["companies"]

    def test_header_fields(self):
        t = self.tender
        self.assertEqual(t["tender_id"], "659460")
        self.assertEqual(t["tender_number"], "3206/2026")
        self.assertEqual(t["ministry"], "Ministry of Municipality")
        self.assertEqual(t["tender_type"], "Public Tender")
        self.assertEqual(t["sector_type"], "Suppliers / Service Providers")
        self.assertEqual(t["entity_tender_number"], "56")
        self.assertEqual(t["envelope_system"], "One Envelope")

    def test_dates_are_iso(self):
        t = self.tender
        self.assertEqual(t["publish_date"], "2026-06-21")
        self.assertEqual(t["closing_date"], "2026-07-12")
        self.assertEqual(t["technical_open_date"], "2026-07-20")
        self.assertEqual(t["awarded_date"], "2026-09-20")

    def test_money_fields(self):
        t = self.tender
        self.assertEqual(t["document_value"], 8000.0)
        self.assertEqual(t["tender_bond"], 80000.0)
        self.assertEqual(t["awarded_amount"], 1819663.33)

    def test_both_roles_are_captured(self):
        roles = {c["role"] for c in self.companies}
        self.assertEqual(roles, {"awarded", "bidder"})
        self.assertEqual(sum(c["role"] == "awarded" for c in self.companies), 2)
        self.assertEqual(sum(c["role"] == "bidder" for c in self.companies), 2)

    def test_award_row_detail(self):
        won = [c for c in self.companies if c["role"] == "awarded"]
        first = won[0]
        self.assertEqual(first["name"], "NAJM ALFARID TRADING")
        self.assertEqual(first["cr_number"], "29309/1")
        self.assertEqual(first["cr_secondary"], "10497")
        self.assertEqual(first["value"], 1481863.33)
        self.assertIn("Exceeding", first["financial_result"])

    def test_second_award_row(self):
        won = [c for c in self.companies if c["role"] == "awarded"]
        self.assertEqual(won[1]["cr_number"], "99457")
        self.assertEqual(won[1]["cr_secondary"], "136933")
        self.assertEqual(won[1]["value"], 337800.0)

    def test_award_values_reconcile_to_the_header_total(self):
        won = [c for c in self.companies if c["role"] == "awarded"]
        self.assertAlmostEqual(sum(c["value"] for c in won),
                               self.tender["awarded_amount"], places=2)

    def test_names_are_normalised_for_matching(self):
        won = [c for c in self.companies if c["role"] == "awarded"]
        self.assertEqual(won[0]["name_normalised"], "najm alfarid trading")

    def test_missing_ids_give_none_not_a_crash(self):
        parsed = parse_detail("<html><body>nothing here</body></html>", "X")
        self.assertIsNone(parsed["tender"]["ministry"])
        self.assertEqual(parsed["companies"], [])
        self.assertFalse(parsed["found"])

    def test_columns_follow_headers_not_positions(self):
        # Swap two columns round; the parser must still read by header.
        swapped = DETAIL.replace(
            "<th>Approved Value</th> <th>Financial Result</th>",
            "<th>Financial Result</th> <th>Approved Value</th>")
        won = [c for c in parse_detail(swapped, "659460")["companies"]
               if c["role"] == "awarded"]
        self.assertEqual(won[0]["financial_result"], "1,481,863.33 QAR")


class Listing(unittest.TestCase):
    def setUp(self):
        self.rows = parse_listing(LISTING)

    def test_all_cards_are_found(self):
        self.assertEqual(len(self.rows), 3)

    def test_ids_come_from_the_report_link(self):
        self.assertEqual([r["tender_id"] for r in self.rows],
                         ["659460", "659015", "658810"])

    def test_card_fields(self):
        r = self.rows[1]
        self.assertEqual(r["tender_number"], "2988/2026")
        self.assertEqual(r["ministry"], "Ministry of Public Health")
        self.assertEqual(r["awarded_date"], "2026-09-18")
        self.assertEqual(r["tender_type"], "Public Tender")
        self.assertEqual(r["tender_bond"], 25000.0)
        self.assertEqual(r["document_value"], 2000.0)

    def test_contractor_sector_is_read(self):
        self.assertEqual(self.rows[2]["sector_type"], "Contractors")

    def test_pager_gives_the_crawl_size(self):
        self.assertEqual(last_listing_page(LISTING, "awarded"), 1210)

    def test_empty_listing_is_empty_not_an_error(self):
        self.assertEqual(parse_listing("<html><body></body></html>"), [])

    def test_kind_from_url(self):
        self.assertEqual(
            kind_from_url("https://monaqasat.mof.gov.qa/TendersOnlineServices/AwardedTenders/3"),
            "awarded")
        self.assertEqual(
            kind_from_url("/TendersOnlineServices/TechnicallyOpenedTenders/1"),
            "technical")
        self.assertIsNone(kind_from_url("/ClassifiedCompaniesProfilesList/1"))


class Firewall(unittest.TestCase):
    def test_block_page_is_detected(self):
        self.assertTrue(looks_rejected(
            "<html><body>The requested URL was rejected. "
            "Your support ID is: 113416</body></html>"))

    def test_real_page_is_not_flagged(self):
        self.assertFalse(looks_rejected(DETAIL))


class Normalisation(unittest.TestCase):
    def test_legal_forms_are_stripped(self):
        self.assertEqual(normalize_name("AL RAYYAN TRADING W L L"),
                         "rayyan trading")
        self.assertEqual(normalize_name("Al-Rayan Trading Co. WLL"),
                         "rayan trading")

    def test_abbreviations_expand(self):
        self.assertEqual(normalize_name("MARINA CONSTR CO WLL"),
                         "marina construction")

    def test_narration_noise_is_removed(self):
        self.assertIn("marina construction",
                      normalize_name("TRF FROM MARINA CONSTRUCTION CO WLL "
                                     "REF 88213"))

    def test_word_order_does_not_matter_for_the_key(self):
        self.assertEqual(canonical_key("GULF PACKAGING INDUSTRIES"),
                         canonical_key("INDUSTRIES PACKAGING GULF"))

    def test_cr_is_split_on_the_pipe(self):
        self.assertEqual(normalize_cr(" 29309/1 | 10497 "),
                         ("29309/1", "10497"))
        self.assertEqual(normalize_cr("99457"), ("99457", None))
        self.assertEqual(normalize_cr(""), (None, None))

    def test_cr_root_drops_the_branch_suffix(self):
        self.assertEqual(cr_root("29309/1"), "29309")
        self.assertEqual(cr_root("99457"), "99457")
        self.assertIsNone(cr_root(None))

    def test_similarity_recognises_variants(self):
        self.assertGreater(
            similarity("NAJM ALFARID TRADING", "Najm Al Farid Trading WLL"),
            0.8)
        self.assertLess(similarity("NAJM ALFARID TRADING",
                                   "Gulf Packaging Industries"), 0.5)


class ExtraLiveFixtures(unittest.TestCase):
    """Shapes that showed up on the live site besides the original 659460 page."""

    def test_unknown_id_is_the_home_page(self):
        html = (FIX / "detail_600000_unknown_id_home.html").read_text(encoding="utf-8")
        parsed = parse_detail(html, "600000")
        self.assertFalse(parsed["found"])
        self.assertEqual(parsed["companies"], [])

    def test_combined_opening_table(self):
        html = (FIX / "detail_661022_combined_stage.html").read_text(encoding="utf-8")
        parsed = parse_detail(html, "661022")
        self.assertTrue(parsed["found"])
        self.assertEqual(parsed["tender"]["tender_number"], "4768/2026")
        self.assertEqual(parsed["tender"]["financial_open_date"],
                         parsed["tender"]["technical_open_date"])
        awarded = [c for c in parsed["companies"] if c["role"] == "awarded"]
        bidders = [c for c in parsed["companies"] if c["role"] == "bidder"]
        self.assertEqual(len(awarded), 1)
        self.assertGreaterEqual(len(bidders), 5)
        self.assertEqual(awarded[0]["cr_number"], "34296")
        self.assertTrue(all(c["stage"] == "technical_financial" for c in bidders))

    def test_split_technical_and_financial_tables(self):
        html = (FIX / "detail_555600_split_stages.html").read_text(encoding="utf-8")
        parsed = parse_detail(html, "555600")
        self.assertTrue(parsed["found"])
        self.assertEqual(parsed["tender"]["financial_open_date"], "2025-11-04")
        stages = {c["stage"] for c in parsed["companies"]}
        self.assertEqual(stages, {"awarded", "technical", "financial"})
        self.assertTrue(all(c["cr_number"] for c in parsed["companies"]))

    def test_direct_agreement_still_has_an_award_row(self):
        html = (FIX / "detail_534222_direct_agreement.html").read_text(encoding="utf-8")
        parsed = parse_detail(html, "534222")
        self.assertTrue(parsed["found"])
        self.assertTrue([c for c in parsed["companies"] if c["role"] == "awarded"])


if __name__ == "__main__":
    unittest.main()
