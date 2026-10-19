"""Reading each list's format, screening, and the load's all-or-nothing rule."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sanctions_lists import fetch, parse  # noqa: E402
from sanctions_lists.cli import main  # noqa: E402
from sanctions_lists.store import Store  # noqa: E402
import fake_lists as F  # noqa: E402


class Formats(unittest.TestCase):
    def test_un_entities_only_with_aliases(self):
        (a, b) = parse.un(F.UN)
        self.assertEqual((a["ref"], a["name"], a["aliases"], a["listed_on"]),
                         ("QDe.999", "NORTHWIND PLASTIC INDUSTRIES", ["Northwind Plastics"], "2015-03-01"))
        self.assertEqual(b["programme"], "DPRK")

    def test_ofac_drops_individuals_and_vessels_and_joins_aliases(self):
        (e,) = parse.ofac(F.SDN, F.SDN_ALT, "OFAC SDN")
        self.assertEqual((e["ref"], e["name"], e["aliases"], e["programme"]),
                         ("36", "NORTHWIND PLASTIC INDUSTRIES W.L.L.", ["NORTHWIND PLASTICS CO."], "SDGT"))

    def test_eu_groups_rows_of_one_enterprise(self):
        (e,) = parse.eu(F.EU)
        self.assertEqual((e["ref"], e["name"], e["aliases"]), ("EU.1.1", "Northwind Plastic Industries", ["Northwind Plastics"]))

    def test_uk_skips_the_report_date_line_and_individuals(self):
        (e,) = parse.uk(F.UK)
        self.assertEqual((e["ref"], e["name"], e["aliases"], e["listed_on"]),
                         ("AQD0999", "NORTHWIND PLASTIC INDUSTRIES", ["NORTHWIND PLASTICS"], "2015-03-01"))


def run(server, *argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(list(argv), session=server)
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.server = F.FakeServer()

    def tearDown(self):
        self.tmp.cleanup()

    def screen(self, *rows):
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n" + "".join(f"{i},{n},1\n" for i, n in rows), encoding="utf-8")
        out = Path(self.tmp.name) / "s.csv"
        run(self.server, "--db", self.db, "match", str(book), "--out", str(out))
        with open(out, encoding="utf-8-sig") as f:
            return {r["customer_id"]: r for r in csv.DictReader(f)}

    def test_load_then_screen(self):
        code, printed = run(self.server, "--db", self.db, "load")
        self.assertEqual(code, 0)
        self.assertIn("UN 2, OFAC SDN 1, OFAC non-SDN 1, EU 1, UK 1", printed)
        rows = self.screen(("C1", "NORTHWIND PLASTIC INDUSTRIES W.L.L"), ("C2", "NORTHWIND STEEL FACTORY"),
                           ("C3", "SUNRISE DATES FACTORY"))
        self.assertEqual(rows["C1"]["sanctions_hit"], "1")
        self.assertEqual(rows["C1"]["sanctions_lists"], "EU; OFAC SDN; OFAC non-SDN; UK; UN")
        self.assertEqual(rows["C2"]["sanctions_hit"], "0")          # same brand, different company
        self.assertEqual(rows["C3"]["sanctions_hit"], "0")
        self.assertLess(float(rows["C3"]["best_score"]), 95)

    def test_a_cut_off_download_fails_the_run_and_keeps_the_lists(self):
        run(self.server, "--db", self.db, "load")
        for key in ("ofac_sdn", "uk"):
            self.server.cut = {key}
            with self.assertRaises(fetch.SiteError):
                run(self.server, "--db", self.db, "load")
        self.server.cut = set()
        self.server.down = {"eu"}
        with self.assertRaises(fetch.SiteError):
            run(self.server, "--db", self.db, "load")
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs(4)], ["error", "error", "error", "ok"])
            self.assertEqual(dict(store.stats())["OFAC SDN"], 1)

    def test_a_connection_error_is_retried(self):
        import requests

        class Flaky(F.FakeServer):
            fails = 2

            def get(self, url, headers=None, timeout=None):
                if url == fetch.FILES["un"][0] and self.fails:
                    self.fails -= 1
                    raise requests.ConnectionError("connect timeout")
                return super().get(url, headers, timeout)

        self.assertIn("UN", parse.un(fetch.download("un", Flaky(), wait=0))[0]["list"])
        with self.assertRaises(fetch.SiteError):
            fetch.download("un", Flaky(), attempts=2, wait=0)

    def test_a_missing_end_marker_is_caught_even_without_content_length(self):
        self.server.bodies["ofac_sdn"] = F.SDN.replace(F.EOF, "")
        with self.assertRaises(fetch.SiteError):
            run(self.server, "--db", self.db, "load")

    def test_a_delisted_entity_is_kept_as_not_listed(self):
        run(self.server, "--db", self.db, "load")
        self.server.bodies["un"] = F.UN.replace("BLUE HARBOUR TRADING", "").replace("KPe.998", "")
        run(self.server, "--db", self.db, "load")
        with Store(self.db) as store:
            self.assertEqual(dict(store.stats())["delisted (kept)"], 1)


if __name__ == "__main__":
    unittest.main()
