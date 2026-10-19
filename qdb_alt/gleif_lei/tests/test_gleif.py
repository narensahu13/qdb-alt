"""Reading LEI records, the CR and name rules, and the commands end to end
against a stand-in API. The fixture pages are made up, in the real JSON:API
shape: an Arabic legal name with a transliterated one, a Qatar Financial
Centre record (its own numbering), a CR with leading zeros, and a duplicate
record of an issued LEI."""

from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from gleif_lei import fetch
    from gleif_lei.cli import main
    from gleif_lei.match import features, index_by_cr, match_one, names_agree
    from gleif_lei.parse import cr_root, record
    from gleif_lei.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gleif_lei import fetch
    from gleif_lei.cli import main
    from gleif_lei.match import features, index_by_cr, match_one, names_agree
    from gleif_lei.parse import cr_root, record
    from gleif_lei.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PAGE_1 = (FIXTURES / "lei_page_1.json").read_text(encoding="utf-8")
PAGE_2 = (FIXTURES / "lei_page_2.json").read_text(encoding="utf-8")


class Response:
    def __init__(self, status, text):
        self.status_code, self.text, self.encoding = status, text, "utf-8"


class FakeSite:
    def __init__(self):
        self.pages = {fetch.page_url(1): PAGE_1, fetch.page_url(2): PAGE_2}
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return Response(200, self.pages[url]) if url in self.pages else Response(404, "")


def all_records():
    return [record(r) for page in (PAGE_1, PAGE_2) for r in json.loads(page)["data"]]


def customer(cid, name, cr, search_name=""):
    return {"customer_id": cid, "name": name, "cr_number": cr, "search_name": search_name}


class Records(unittest.TestCase):
    def test_a_record_becomes_one_flat_row(self):
        rows = {r["lei"]: r for r in all_records()}
        self.assertEqual(rows["2763000000000000AA02"]["other_names"], '["NORTHWIND PLASTIC INDUSTRIES"]')
        self.assertEqual((rows["2763000000000000AA02"]["legal_form"], rows["2763000000000000AA02"]["hq_city"],
                          rows["2763000000000000AA02"]["initial_registration"]),
                         ("Private Shareholding Company", "Al Wakrah", "2021-06-15"))
        self.assertEqual(rows["2763000000000000AA03"]["cr_root"], "11111")
        # the QFC numbers its own companies: never a CR to join on
        self.assertEqual((rows["2763000000000000AA04"]["registered_as"], rows["2763000000000000AA04"]["cr_root"]),
                         ("00326", None))
        self.assertEqual(rows["2763000000000000AA04"]["legal_form"], "Limited Liability Company (QFC)")
        self.assertIsNone(rows["2763000000000000AA05"]["legal_form"])          # 9999: not given

    def test_cr_root(self):
        self.assertEqual(cr_root("000000019269"), "19269")
        self.assertEqual(cr_root("0000012091/5"), "12091")
        self.assertEqual(cr_root(" 12091 / 5 "), "12091")
        for odd in ("01-0096-00", "01 0118 00", "", None, "0000", "QFC/00326"):
            self.assertIsNone(cr_root(odd), odd)


class Matching(unittest.TestCase):
    def setUp(self):
        self.records = all_records()
        self.by_cr = index_by_cr(self.records)

    def match(self, c):
        return match_one(c, self.records, self.by_cr)

    def test_by_cr_the_issued_lei_wins_over_a_duplicate_record(self):
        m = self.match(customer("C1", "SUNRISE DATES FACTORY", "000000012345"))
        self.assertEqual((m["lei"], m["match_method"]), ("2763000000000000AA01", "cr"))

    def test_by_cr_against_the_transliterated_name(self):
        m = self.match(customer("C2", "Northwind Plastic Industries W.L.L", "000000022222"))
        self.assertEqual((m["lei"], m["match_method"], m["matched_name"]),
                         ("2763000000000000AA02", "cr", m["_record"]["legal_name"]))

    def test_a_branch_does_not_take_its_parents_lei(self):
        m = self.match(customer("C3", "BLUE LAGOON ICE", "0000011111/2"))
        self.assertIsNone(m["lei"])

    def test_a_branch_with_the_same_name_does(self):
        m = self.match(customer("C3b", "Golden Harbour Trading - Branch", "0000011111/2"))
        self.assertEqual((m["lei"], m["match_method"]), ("2763000000000000AA03", "cr"))

    def test_by_name_when_the_record_has_no_cr(self):
        m = self.match(customer("C4", "Blue Harbour Ice Factory", "000000033333"))
        self.assertEqual((m["lei"], m["match_method"], m["matched_name"]),
                         ("2763000000000000AA04", "name", "Blue Harbour Ice Factory LLC"))

    def test_not_by_name_when_the_crs_differ(self):
        m = self.match(customer("C5", "Golden Harbour Trading Co", "000000044444"))
        self.assertIsNone(m["lei"])

    def test_names_agree(self):
        self.assertTrue(names_agree(["THE NATIONAL AGRICULTURAL AND LIVESTOCK PROCESSING GROUP"],
                                    ["NATIONAL GROUP FOR AGRICULTURE AND ANIMAL PRODUCTS", "MAZZRATY"]))
        self.assertTrue(names_agree(["Al Hudaifi Steel"], ["AL HODAIFI STEEL FACTORY"]))
        for ours, theirs in [("AL TAMAKON FOR COMPREHENSIVE EDUCATION", "ABDULLA AL MULLA TRADING"),
                             ("Oryx for PET Preforms Factory", "WESTBAY ICE AND WATER FACTORY"),
                             ("Qatar Steel Industries Factory", "Qatar Plastic Industries Factory")]:
            self.assertFalse(names_agree([ours], [theirs]), (ours, theirs))

    def test_features(self):
        m = self.match(customer("C2", "Northwind Plastic Industries", "22222"))
        f = features(m, today=date(2026, 6, 15))
        self.assertEqual((f["lei_exists"], f["lei_status_issued"], f["lei_lapsed"], f["lei_years_since_registration"],
                          f["lei_legal_form"]), (1, 0, 1, 5.0, "Private Shareholding Company"))
        f = features(self.match(customer("C9", "Nobody Here", "99999")))
        self.assertEqual(f["lei_exists"], 0)
        self.assertTrue(all(f[k] is None for k in ("lei_status_issued", "lei_years_since_registration", "lei_lapsed",
                                                   "lei_legal_form", "matched_name", "match_method", "lei")))


def run(site, *argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(list(argv), session=site)
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.site = FakeSite()

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return run(self.site, "--db", self.db, "load", "--delay", "0")

    def test_load_and_match(self):
        code, printed = self.load()
        self.assertEqual(code, 0)
        self.assertIn("5 records, 3 issued, 4 with a Qatar CR", printed)
        self.assertEqual(self.site.calls, [fetch.page_url(1), fetch.page_url(2)])
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,SUNRISE DATES FACTORY,000000012345\n"
                        "C2,NORTHWIND PLASTIC INDUSTRIES W.L.L,000000022222\n"
                        "C3,BLUE LAGOON ICE,0000011111/2\n"
                        "C4,Blue Harbour Ice Factory,000000033333\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(self.site, "--db", self.db, "match", str(book), "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("3 of 4 customers have an LEI (2 by CR)", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        self.assertEqual((rows["C1"]["lei_exists"], rows["C1"]["lei_status_issued"], rows["C1"]["lei_lapsed"],
                          rows["C1"]["lei_legal_form"], rows["C1"]["match_method"]),
                         ("1", "1", "0", "Limited Liability Company", "cr"))
        self.assertEqual((rows["C2"]["lei_status_issued"], rows["C2"]["lei_lapsed"]), ("0", "1"))
        self.assertEqual((rows["C3"]["lei_exists"], rows["C3"]["lei"], rows["C3"]["lei_lapsed"]), ("0", "", ""))
        self.assertEqual((rows["C4"]["match_method"], rows["C4"]["lei"]), ("name", "2763000000000000AA04"))
        with Store(self.db) as store:
            self.assertEqual([(r["command"], r["status"], r["rows"]) for r in store.runs()],
                             [("match", "ok", 4), ("load", "ok", 5)])
            self.assertEqual(dict(store.stats())["customers with an LEI"], 3)

    def test_a_record_that_disappears_is_kept_as_not_listed(self):
        self.load()
        page = json.loads(PAGE_2)
        page["data"] = page["data"][1:]
        page["meta"]["pagination"]["total"] = 4
        self.site.pages[fetch.page_url(2)] = json.dumps(page)
        code, printed = self.load()
        self.assertIn("4 records, 2 issued, 4 with a Qatar CR (1 dropped)", printed)
        with Store(self.db) as store:
            self.assertNotIn("2763000000000000AA04", [r["lei"] for r in store.records()])
            gone = store.db.execute("SELECT listed FROM lei_record WHERE lei = '2763000000000000AA04'").fetchone()
            self.assertEqual(gone[0], 0)

    def test_a_failed_page_fails_the_run_and_changes_nothing(self):
        self.load()
        self.site.pages[fetch.page_url(1)] = PAGE_1.replace("SUNRISE DATES FACTORY W.L.L", "RENAMED")
        del self.site.pages[fetch.page_url(2)]
        with self.assertRaises(fetch.SiteError):
            self.load()
        with Store(self.db) as store:
            self.assertEqual(store.runs(1)[0]["status"], "error")
            self.assertIn("HTTP 404", store.runs(1)[0]["error"])
            self.assertEqual(len(store.records()), 5)
            self.assertNotIn("RENAMED", [r["legal_name"] for r in store.records()])

    def test_an_error_page_or_a_short_count_fails_the_run(self):
        self.site.pages[fetch.page_url(2)] = "<html>Service unavailable</html>"
        with self.assertRaises(fetch.SiteError):
            self.load()
        self.site.pages[fetch.page_url(2)] = PAGE_2.replace('"total": 5', '"total": 6')
        with self.assertRaises(fetch.SiteError):
            self.load()
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs()], ["error", "error"])
            self.assertEqual(store.records(), [])

    def test_match_before_load(self):
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("name,cr_number\nSUNRISE DATES FACTORY,12345\n", encoding="utf-8")
        self.assertEqual(run(self.site, "--db", self.db, "match", str(book))[0], 2)


if __name__ == "__main__":
    unittest.main()
