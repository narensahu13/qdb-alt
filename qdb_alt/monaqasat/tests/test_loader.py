"""Customer lists as they actually arrive: out of Excel, on Windows."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monaqasat.match import detect_encoding, load_customers_report

ARABIC_NAME = "شركة الريان للتجارة"


class Encodings(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _csv(self, text: str, encoding: str, name="c.csv") -> Path:
        p = self.dir / name
        p.write_bytes(text.encode(encoding))
        return p

    def test_your_error_byte_0xbf_deep_in_a_file(self):
        """UnicodeDecodeError: 'utf-8' codec can't decode byte 0xbf in
        position 6376. A Windows-1252 file with a character UTF-8 rejects,
        far enough in that the first rows looked fine."""
        rows = ["customer_id,name,cr_number"]
        rows += [f"C{i:04d},TRADING COMPANY {i},{10000 + i}" for i in range(200)]
        rows.append("C9999,COMPANIA ¿ESPECIAL?,99999")
        p = self._csv("\n".join(rows), "cp1252")
        raw = p.read_bytes()
        self.assertGreater(raw.index(b"\xbf"), 6000)

        out, rep = load_customers_report(p)
        self.assertEqual(len(out), 201)
        self.assertIn("1252", rep["encoding"])
        self.assertEqual(out[-1]["name"], "COMPANIA ¿ESPECIAL?")

    def test_arabic_excel_csv(self):
        text = f"customer_id,name,name_ar,cr_number\nC1,AL RAYAN TRADING,{ARABIC_NAME},12345\n"
        out, rep = load_customers_report(self._csv(text, "cp1256"))
        self.assertIn("1256", rep["encoding"])
        self.assertEqual(out[0]["name_ar"], ARABIC_NAME)

    def test_excel_csv_utf8_with_bom(self):
        text = f"customer_id,name,name_ar,cr_number\nC1,AL RAYAN,{ARABIC_NAME},12345\n"
        out, rep = load_customers_report(self._csv(text, "utf-8-sig"))
        self.assertEqual(rep["encoding"], "utf-8")
        self.assertEqual(out[0]["customer_id"], "C1")     # BOM not in header
        self.assertEqual(out[0]["name_ar"], ARABIC_NAME)

    def test_utf8_with_one_stray_byte_keeps_the_arabic(self):
        """Decoding this as a Windows code page would mangle every Arabic
        name to fix one bad byte."""
        good = "\n".join(f"C{i},CO {i},{ARABIC_NAME},{i}" for i in range(40))
        raw = ("customer_id,name,name_ar,cr_number\n" + good).encode("utf-8")
        raw = raw.replace(b"CO 7,", b"CO 7\xbf,", 1)
        p = self.dir / "mixed.csv"
        p.write_bytes(raw)
        out, rep = load_customers_report(p)
        self.assertIn("replacements", rep["encoding"])
        self.assertEqual(out[0]["name_ar"], ARABIC_NAME)
        self.assertTrue(any("line" in w for w in rep["warnings"]))

    def test_plain_ascii_is_utf8(self):
        _, enc, _ = detect_encoding(b"customer_id,name\nC1,ACME\n")
        self.assertEqual(enc, "utf-8")


class Headers(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_core_banking_style_headers(self):
        p = self.dir / "c.csv"
        p.write_text("CIF No,Customer Name,C.R. No.\n001,AL RAYAN,12345\n",
                     encoding="utf-8")
        out, rep = load_customers_report(p)
        self.assertEqual(rep["columns"], {"customer_id": "CIF No",
                                          "name": "Customer Name",
                                          "cr_number": "C.R. No."})
        self.assertEqual(out[0], {"customer_id": "001", "name": "AL RAYAN",
                                  "cr_number": "12345", "name_ar": None})

    def test_semicolon_delimited(self):
        p = self.dir / "c.csv"
        p.write_text("customer_id;name;cr_number\nC1;ACME TRADING;555\n",
                     encoding="utf-8")
        out, _ = load_customers_report(p)
        self.assertEqual(out[0]["cr_number"], "555")

    def test_commas_inside_quoted_names(self):
        p = self.dir / "c.csv"
        p.write_text('customer_id,name,cr_number\nC1,"ACME, TRADING & CO",555\n',
                     encoding="utf-8")
        out, _ = load_customers_report(p)
        self.assertEqual(out[0]["name"], "ACME, TRADING & CO")

    def test_unusable_file_says_why(self):
        p = self.dir / "c.csv"
        p.write_text("account,balance\n1,100\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_customers_report(p)
        self.assertIn("account", str(ctx.exception))
        self.assertIn("cr_number", str(ctx.exception))

    def test_no_cr_column_is_allowed_but_flagged(self):
        p = self.dir / "c.csv"
        p.write_text("name\nACME\n", encoding="utf-8")
        out, rep = load_customers_report(p)
        self.assertEqual(len(out), 1)
        self.assertTrue(any("names alone" in w for w in rep["warnings"]))

    def test_empty_rows_are_skipped_and_counted(self):
        p = self.dir / "c.csv"
        p.write_text("customer_id,name,cr_number\nC1,ACME,1\n,,\nC2,BETA,2\n",
                     encoding="utf-8")
        out, rep = load_customers_report(p)
        self.assertEqual(len(out), 2)
        self.assertEqual(rep["skipped (no name, no CR)"], 1)


class Excel(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "book.xlsx"

    def tearDown(self):
        self.tmp.cleanup()

    def _book(self, rows, sheet="Customers", blank_top=0):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = sheet
        for _ in range(blank_top):
            ws.append([])
        for r in rows:
            ws.append(r)
        wb.save(self.path)

    def test_xlsx_is_read_directly(self):
        self._book([["customer_id", "name", "name_ar", "cr_number"],
                    ["C1", "AL RAYAN", ARABIC_NAME, "12345"]])
        out, rep = load_customers_report(self.path)
        self.assertEqual(rep["encoding"], "excel workbook")
        self.assertEqual(out[0]["name_ar"], ARABIC_NAME)

    def test_numeric_cr_is_not_turned_into_a_float(self):
        """Excel stores 12345 as 12345.0; that would never match a CR."""
        self._book([["customer_id", "name", "cr_number"],
                    [1001, "ACME", 12345]])
        out, _ = load_customers_report(self.path)
        self.assertEqual(out[0]["cr_number"], "12345")
        self.assertEqual(out[0]["customer_id"], "1001")

    def test_title_rows_above_the_header(self):
        self._book([["customer_id", "name", "cr_number"],
                    ["C1", "ACME", "1"]], blank_top=2)
        out, _ = load_customers_report(self.path)
        self.assertEqual(out[0]["name"], "ACME")

    def test_named_sheet(self):
        self._book([["name", "cr_number"], ["ACME", "1"]], sheet="Book")
        out, _ = load_customers_report(self.path, sheet="Book")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
