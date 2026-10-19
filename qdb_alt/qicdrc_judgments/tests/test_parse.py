"""Reading a judgment page and the sitemap. The fixture page is made up, in the
site's real markup: the parties in the heading and (more fully) in the title,
then court, case number, citation, date and keywords, also given as JSON-LD."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qicdrc_judgments.parse import (is_organisation, parse_judgment, sitemap, split_case_name,
                                        title_parties)
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qicdrc_judgments.parse import (is_organisation, parse_judgment, sitemap, split_case_name,
                                        title_parties)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_site import APPEAL, APPEAL_PAGE, FIXTURES, SUNRISE  # noqa: E402


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (FIXTURES / "judgment_page.html").read_text(encoding="utf-8")
        cls.j = parse_judgment(cls.page, SUNRISE)

    def test_case_details_come_from_the_json_ld(self):
        j = self.j
        self.assertEqual((j["case_number"], j["citation"], j["court"], j["judgment_date"], j["year"]),
                         ("CTFIC0099/2025", "[2025] QIC (F) 99", "Court of First Instance", "2025-11-03", 2025))
        self.assertEqual(j["keywords"], "First Instance Circuit; Default Judgment; Breach of Contract")
        self.assertEqual(j["status"], "Positive / Neutral Judicial Consideration")

    def test_the_same_details_from_the_html_when_there_is_no_json_ld(self):
        without = self.page.split('<script type="application/ld+json">')[0] + self.page.split("</script>")[-1]
        j = parse_judgment(without, SUNRISE)
        self.assertEqual((j["case_number"], j["citation"], j["court"], j["judgment_date"]),
                         (self.j["case_number"], self.j["citation"], self.j["court"], self.j["judgment_date"]))
        self.assertEqual(j["keywords"], self.j["keywords"])

    def test_companies_are_kept_from_the_heading_and_the_fuller_title(self):
        self.assertEqual(self.j["parties"], [
            {"side": "claimant", "name": "Sunrise Dates Factory W.L.L"},
            {"side": "defendant", "name": "Blue Harbour Ice LLC"},
            {"side": "defendant", "name": "Pearl Coast Trading & Contracting Co"},
        ])

    def test_people_are_counted_once_and_not_kept(self):
        # "Mr Khalid Example" in the heading is "Khalid Example" in the title
        self.assertEqual(self.j["n_individual_parties"], 2)
        self.assertEqual(self.j["case_title"],
                         "Sunrise Dates Factory W.L.L, 1 individual v Blue Harbour Ice LLC, "
                         "Pearl Coast Trading & Contracting Co, 1 individual")
        stored = repr({k: v for k, v in self.j.items() if k != "url"})
        self.assertNotIn("Khalid", stored)
        self.assertNotIn("Fahad", stored)

    def test_outcome_only_from_the_courts_keywords(self):
        self.assertEqual(self.j["outcome"], "default judgment")
        appeal = parse_judgment(APPEAL_PAGE, APPEAL)
        self.assertEqual(appeal["outcome"], "appeal dismissed")
        self.assertEqual([(p["side"], p["name"]) for p in appeal["parties"]],
                         [("claimant", "Blue Harbour Ice LLC"), ("defendant", "Sunrise Dates Factory WLL")])
        self.assertEqual((appeal["case_number"], appeal["judgment_date"]), ("CTAD0005/2025", "2026-01-15"))

    def test_a_page_without_case_details_is_an_error(self):
        with self.assertRaises(ValueError):
            parse_judgment("<html><title>Page not found | QICDRC</title></html>", SUNRISE)


class CaseNames(unittest.TestCase):
    def test_sides_and_others(self):
        self.assertEqual(split_case_name("John and Wiedeman LLC v Trimoo Parks LLC and Others"),
                         [("claimant", "John and Wiedeman LLC"), ("defendant", "Trimoo Parks LLC")])
        self.assertEqual(split_case_name("Dentons & Co (QFC Branch) V  Bin Omran Trading & Contracting LLC"),
                         [("claimant", "Dentons & Co (QFC Branch)"),
                          ("defendant", "Bin Omran Trading & Contracting LLC")])

    def test_a_company_name_with_and_in_it_is_not_cut(self):
        for name in ["Al Wajba Dairy and Juices Factory", "Gulf GRC and Trading Co", "John and Wiedeman LLC"]:
            self.assertEqual(split_case_name(f"{name} v QFCRA")[0], ("claimant", name))

    def test_lists_of_parties_are_cut(self):
        self.assertEqual([n for _, n in split_case_name("A Holding LLC against B LLC, C LLC and D LLC")],
                         ["A Holding LLC", "B LLC", "C LLC", "D LLC"])
        self.assertEqual([n for _, n in split_case_name("(1) ALPHA CO. LLC (2) SHEIKH X BIN Y V BETA BANK LLC")],
                         ["ALPHA CO. LLC", "SHEIKH X BIN Y", "BETA BANK LLC"])
        self.assertEqual([n for _, n in split_case_name("Al Wajba Dairy and Juices Factory and Mr X v QFCRA")],
                         ["Al Wajba Dairy and Juices Factory", "Mr X", "QFCRA"])

    def test_case_numbers_and_notes_in_a_heading_are_not_parties(self):
        self.assertEqual(split_case_name("CTAD0002/2022 (on appeal from Case No. CTFIC 00019/2021) "
                                         "Example Holding LLC v Beta Trading WLL"),
                         [("claimant", "Example Holding LLC"), ("defendant", "Beta Trading WLL")])
        self.assertEqual(split_case_name("Kilo Bank LLC (Permission to Appeal Applications) v Delta Co - Corrigendum"),
                         [("claimant", "Kilo Bank LLC"), ("defendant", "Delta Co")])
        self.assertEqual(split_case_name("Example Wealth LLC (In Liquidation)"), [("party", "Example Wealth LLC")])
        self.assertEqual(split_case_name("RE Practice Direction No. 1 of 2024 (Litigation Restraint Orders) "
                                         "In the Matters of Example Limited and Mr Some One"),
                         [("party", "Example Limited"), ("party", "Mr Some One")])

    def test_a_heading_naming_several_cases(self):
        self.assertEqual(split_case_name("Alpha LLC v Beta WLL; Alpha LLC v Gamma Co"),
                         [("claimant", "Alpha LLC"), ("defendant", "Beta WLL"),
                          ("claimant", "Alpha LLC"), ("defendant", "Gamma Co")])
        self.assertEqual([n for _, n in split_case_name("and CTFIC0033/2024 Person Two v Beta WLL "
                                                        "and Person Three v Beta WLL")],
                         ["Person Two", "Beta WLL", "Beta WLL", "Person Three"])

    def test_no_v_means_no_sides(self):
        self.assertEqual(split_case_name("Example Trading WLL"), [("party", "Example Trading WLL")])
        self.assertEqual(split_case_name("IN THE LIQUIDATION OF EXAMPLE GLOBE LLC"), [("party", "EXAMPLE GLOBE LLC")])
        self.assertEqual(split_case_name("In the matter of Example Holding LLC"), [("party", "Example Holding LLC")])

    def test_parties_in_a_title_after_the_case_number(self):
        self.assertEqual(title_parties("CTAD0006/2021 - A LLC against B LLC | Judgements | QICDRC"),
                         "A LLC against B LLC")
        self.assertEqual(title_parties("Case No. 2 of 2020 - [2020] QIC (F) 7 | Judgements | QICDRC"), "")
        self.assertEqual(title_parties("CTFIC0025/2022"), "")


class Organisations(unittest.TestCase):
    def test_organisations(self):
        for name in ["Meinhardt Bim Studios LLC", "QFCRA", "Qatar Financial Centre Regulatory Authority",
                     "ADABISC LLC", "Gulf GRC and Trading Co", "Dentons & Co (QFC Branch)", "Lesha Bank"]:
            self.assertTrue(is_organisation(name), name)

    def test_people(self):
        for name in ["DILIP KUMAR KUNDU", "Sheikh Nasser bin Abdulrahman bin Nasser Al Thani", "A", "MML",
                     "Mr Khalid Example"]:
            self.assertFalse(is_organisation(name), name)


class Sitemap(unittest.TestCase):
    def test_english_judgment_pages_only(self):
        pages, children = sitemap((FIXTURES / "sitemap.xml").read_text(encoding="utf-8"))
        self.assertEqual(pages, [(SUNRISE, "2026-08-07T16:15:30+03:00"), (APPEAL, "2026-02-01T10:00:00+03:00")])
        self.assertEqual(children, [])

    def test_a_sitemap_index_gives_its_children(self):
        xml = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               "<sitemap><loc>https://www.qicdrc.gov.qa/sitemap.xml?page=2</loc></sitemap></sitemapindex>")
        self.assertEqual(sitemap(xml), ([], ["https://www.qicdrc.gov.qa/sitemap.xml?page=2"]))


if __name__ == "__main__":
    unittest.main()
