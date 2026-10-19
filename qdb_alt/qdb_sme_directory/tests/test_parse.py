"""Reading profiles out of the directory's text layer.

The fixture is a made-up four-page directory in the real layout: a table of
contents, a profile with its name above the Product Names marker (wrapped over
two lines), a profile laid out the other way round with its name closing the
page, a short profile, and a page that is not a profile at all.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qdb_sme_directory.parse import extract_text, parse_text, profile_name, table_of_contents
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qdb_sme_directory.parse import extract_text, parse_text, profile_name, table_of_contents

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "directory_sample.txt"


class Profiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.profiles = {p["page"]: p for p in parse_text(cls.text, "manufacturing")}

    def test_one_record_per_profile_page_and_none_for_other_pages(self):
        self.assertEqual(sorted(self.profiles), [2, 3, 4])

    def test_table_of_contents_pairs_names_with_pages_and_drops_the_sector_heading(self):
        toc = table_of_contents(self.text.split("\f"))
        self.assertEqual(toc, {2: "Sunrise Dates Factory", 3: "Blue Harbour Ice", 4: "Northwind Plastic Industries"})

    def test_the_listed_name_wins_and_the_page_name_is_kept_as_an_alias(self):
        p = self.profiles[2]
        self.assertEqual(p["name"], "Sunrise Dates Factory")
        self.assertIn("Sunrise Dates Processing Factory", p["titles"])

    def test_name_above_the_marker_is_joined_across_wrapped_lines(self):
        lines = ["Some description ends here.", "Sunrise Dates Processing", "Factory",
                 ":أسماء المنتجاتProduct Names:"]
        self.assertEqual(profile_name(lines), "Sunrise Dates Processing Factory")

    def test_name_closing_the_page_when_only_arabic_is_above_the_marker(self):
        page = self.text.split("\f")[2]
        self.assertEqual(profile_name([l.strip() for l in page.split("\n")]), "Blue Harbour Ice")

    def test_fields(self):
        a, b, c = self.profiles[2], self.profiles[3], self.profiles[4]
        self.assertEqual((a["website"], a["founded_year"], a["exporter"]), ("sunrisedates.qa", 2015, 1))
        self.assertEqual(a["hs_codes"], ["08041000", "20079910"])
        self.assertEqual(a["products"], ["Dates Paste", "Date Syrup"])
        # "Established on May 3, 2011": the year is found past the day of the month
        self.assertEqual((b["website"], b["founded_year"], b["exporter"]), ("blueharbourice.com", 2011, 0))
        self.assertEqual((c["website"], c["founded_year"], c["hs_codes"]), (None, None, ["39232100"]))

    def test_contact_people_are_not_kept(self):
        kept = repr(list(self.profiles.values()))
        for private in ("Jane Example", "John Example", "General Manager", "55000001", "info@sunrisedates.qa"):
            self.assertNotIn(private, kept)


class FromPdf(unittest.TestCase):
    def test_extract_text_puts_a_form_feed_between_pages(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest("pymupdf is not installed")
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "two_pages.pdf"
            doc = pymupdf.open()
            for line in ("Sunrise Dates Factory", "Blue Harbour Ice"):
                doc.new_page().insert_text((72, 72), line)
            doc.save(pdf)
            doc.close()
            pages = extract_text(pdf).split("\f")
        self.assertEqual([p.strip() for p in pages], ["Sunrise Dates Factory", "Blue Harbour Ice"])


if __name__ == "__main__":
    unittest.main()
