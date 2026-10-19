"""The commands end to end, offline: load the sample directory, match a small
customer book, read the run log."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from qdb_sme_directory.cli import main
    from qdb_sme_directory.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qdb_sme_directory.cli import main
    from qdb_sme_directory.store import Store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "directory_sample.txt"


def run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.book = Path(self.tmp.name) / "book.csv"
        self.book.write_text(
            "customer_id,name,cr_number\n"
            "C1,SUNRISE DATES FACTORY W.L.L,000000012345\n"
            "C2,BLUE HARBOUR ICE CO,0000054321\n"
            "C3,GOLDEN HARBOUR ICE,000000011111\n"      # shares the generic words, not the brand
            "C4,NORTHWIND PLASTIC INDUSTRIES W.L.L,000000022222\n",
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_match_and_the_run_log(self):
        code, _ = run("--db", self.db, "load", "--text", str(FIXTURE))
        self.assertEqual(code, 0)
        out = Path(self.tmp.name) / "features.csv"
        code, printed = run("--db", self.db, "match", str(self.book), "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("3 of 4 customers found", printed)

        rows = {r["customer_id"]: r for r in csv.DictReader(open(out, encoding="utf-8-sig"))}
        self.assertEqual(rows["C1"]["qdb_dir_listed"], "1")
        self.assertEqual((rows["C1"]["qdb_dir_exporter"], rows["C1"]["qdb_dir_founded_year"],
                          rows["C1"]["qdb_dir_hs_codes"], rows["C1"]["qdb_dir_website"]),
                         ("1", "2015", "2", "sunrisedates.qa"))
        self.assertEqual(rows["C2"]["matched_profile"], "Blue Harbour Ice")
        self.assertEqual(rows["C3"]["qdb_dir_listed"], "0")
        self.assertEqual(rows["C3"]["qdb_dir_founded_year"], "")      # not found: empty, not zero
        self.assertEqual(rows["C4"]["matched_profile"], "Northwind Plastic Industries")

        with Store(self.db) as store:
            log = store.runs()
        self.assertEqual([r["command"] for r in log], ["match", "load manufacturing"])
        self.assertTrue(all(r["status"] == "ok" and r["finished_at"] and r["seconds"] is not None for r in log))
        self.assertEqual(log[1]["rows"], 3)
        self.assertEqual(log[0]["note"], "3 of 4 customers found")

    def test_a_failed_load_is_logged_with_its_error(self):
        with self.assertRaises(FileNotFoundError):
            run("--db", self.db, "load", "--text", str(Path(self.tmp.name) / "missing.txt"))
        with Store(self.db) as store:
            (last,) = store.runs(1)
        self.assertEqual(last["status"], "error")
        self.assertIn("FileNotFoundError", last["error"])

    def test_match_before_load_says_what_to_do(self):
        code, _ = run("--db", self.db, "match", str(self.book))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
