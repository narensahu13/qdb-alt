"""The parser, against responses captured from the live portal."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    from icv import parse
except ImportError:                                   # run from the package dir
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from icv import parse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Listing(unittest.TestCase):
    def setUp(self):
        self.payload = load("listing_page.json")
        self.rows = [parse.company(r) for r in parse.records(self.payload)]

    def test_every_record_becomes_a_row(self):
        self.assertEqual(len(self.rows), 3)
        self.assertTrue(all(self.rows))

    def test_the_fields_a_credit_file_needs(self):
        epo = next(r for r in self.rows if r["cr_number"] == "185319")
        self.assertEqual(epo["name"], "EPO POWER AND CONTROL TRADING")
        self.assertEqual(epo["total_score"], 18.97)
        self.assertEqual(epo["status"], "Valid")
        self.assertEqual(epo["status_code"], 100000002)
        self.assertEqual(epo["industry"], "Engineering and Construction")
        self.assertEqual(epo["expiry_date"], "2027-12-23")
        self.assertEqual(epo["id"], "ac7c7d49-c0d6-ee11-904d-000d3a2e4a22")

    def test_latest_certificate_is_a_number_not_a_date(self):
        # It is a lookup whose Name is the certificate number. Reading it as
        # a date silently produces nothing, and the list is sorted by it.
        epo = next(r for r in self.rows if r["cr_number"] == "185319")
        self.assertEqual(epo["certificate_no"], "10011086")

    def test_a_missing_attribute_is_not_an_error(self):
        # icv_ismanufacturer is present on one record and absent from the
        # other two; absent means unknown, not False.
        flags = sorted((r["manufacturer"] for r in self.rows),
                       key=lambda v: (v is not None, v))
        self.assertEqual(flags, [None, None, False])

    def test_item_count_is_not_the_number_of_companies(self):
        # The portal reports 5000 whatever the truth is; a full sweep finds
        # 6,892. Only MoreRecords may be believed.
        self.assertEqual(self.payload["ItemCount"], 5000)
        self.assertTrue(parse.more(self.payload))

    def test_paging_cookie_is_empty_on_the_guest_list(self):
        self.assertFalse(self.payload["NextPagePagingCookie"])


class History(unittest.TestCase):
    def setUp(self):
        self.rows = [parse.history(r)
                     for r in parse.records(load("score_history.json"))]

    def test_the_whole_published_series(self):
        self.assertEqual(len(self.rows), 6)
        self.assertEqual([r["score"] for r in self.rows],
                         [10.84, 10.85, 17.85, 18.91, 19.13, 18.97])

    def test_the_reason_survives_the_portal_s_whitespace(self):
        # "Transitional Percentage Added  " has two trailing spaces in the
        # source data; grouping by reason would otherwise split in two.
        reasons = {r["reason"] for r in self.rows}
        self.assertIn("Transitional Percentage Added", reasons)
        self.assertTrue(all(r["reason"] == r["reason"].strip()
                            for r in self.rows))

    def test_a_policy_change_is_distinguishable_from_performance(self):
        policy = [r for r in self.rows
                  if r["reason"] == "Transitional Percentage Added"]
        self.assertEqual(len(policy), 1)
        self.assertEqual(policy[0]["change"], 7.0)

    def test_dates_are_utc_not_doha_local(self):
        # The newest row renders as 24/09/2026 02:01 in Doha and is
        # 2026-09-23T23:01:12Z -- a different calendar day. Mixing the two
        # puts an observation in the wrong month at a month end.
        newest = self.rows[-1]
        self.assertEqual(newest["posted_at"], "2026-09-23T23:01:12Z")
        self.assertEqual(newest["posted_date"], "2026-09-23")

    def test_every_row_has_the_portal_s_own_key(self):
        ids = {r["id"] for r in self.rows}
        self.assertEqual(len(ids), 6)


class Page(unittest.TestCase):
    def setUp(self):
        self.html = (FIXTURES / "detail_page.html").read_text(encoding="utf-8")

    def test_the_token_comes_off_the_page(self):
        self.assertTrue(parse.verification_token(self.html))

    def test_the_history_subgrid_is_found_by_relationship(self):
        cfg = parse.history_config(self.html)
        self.assertEqual(cfg.relationship, parse.REL_HISTORY)
        self.assertEqual(cfg.sort, "icv_posteddate ASC")
        self.assertEqual(cfg.entity_id,
                         "ac7c7d49-c0d6-ee11-904d-000d3a2e4a22")

    def test_goods_and_services_are_two_grids_on_one_relationship(self):
        self.assertEqual(len(parse.goods_services_configs(self.html)), 2)

    def test_the_request_body_has_what_power_pages_wants(self):
        body = parse.history_config(self.html).body(page=2, page_size=50)
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["pageSize"], 50)
        self.assertEqual(body["entityName"], "account")
        for key in ("base64SecureConfiguration", "sortExpression", "search",
                    "pagingCookie", "filter", "metaFilter", "nlSearchFilter",
                    "timezoneOffset", "customParameters", "entityId"):
            self.assertIn(key, body)

    def test_a_block_page_says_so_rather_than_parsing_to_nothing(self):
        with self.assertRaises(parse.PageShapeError):
            parse.verification_token("<html><body>403 Forbidden</body></html>")
        with self.assertRaises(parse.PageShapeError):
            parse.grid_configs("<html><body>Sign in</body></html>")


if __name__ == "__main__":
    unittest.main()
