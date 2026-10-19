"""Reading the list rows, and the commands end to end against a stand-in site.
The fixture is a made-up list in the shape the SharePoint endpoint returns:
licences as "English;Arabic" pairs, and a URL that is sometimes plain text and
sometimes a hyperlink object."""

from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from mwani_port_operators import fetch
    from mwani_port_operators.cli import main
    from mwani_port_operators.parse import operator
    from mwani_port_operators.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mwani_port_operators import fetch
    from mwani_port_operators.cli import main
    from mwani_port_operators.parse import operator
    from mwani_port_operators.store import Store

LIST = (Path(__file__).resolve().parents[1] / "fixtures" / "shipping_lines.json").read_text(encoding="utf-8")


class Response:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSite:
    def __init__(self, body=LIST, status=200):
        self.body, self.status = body, status

    def get(self, url, headers=None, timeout=None):
        assert url == fetch.LIST_URL and headers["Accept"].startswith("application/json")
        return Response(self.status, self.body)


class Rows(unittest.TestCase):
    def test_licences_website_and_listing_date(self):
        rows = json.loads(LIST)["value"]
        a, b, c = (operator(r) for r in rows)
        self.assertEqual(a["licences"], ["Clearing Agencies", "Shipping Agencies"])
        self.assertEqual((a["website"], a["listed_since"]), ("http://www.sunrise-shipping.example", "2018-05-22"))
        self.assertIsNone(b["website"])
        self.assertEqual(c["website"], "https://northwind.example")      # hyperlink column

    def test_contact_details_are_not_kept(self):
        rec = operator({**json.loads(LIST)["value"][0], "Phone": "+974 0000", "Email": "someone@example.com"})
        self.assertNotIn("someone@example.com", repr(rec))


def run(site, *argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(list(argv), session=site)
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_and_match(self):
        self.assertEqual(run(FakeSite(), "--db", self.db, "load")[0], 0)
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,NORTHWIND WASTE TREATMENT W.L.L.,000000069821\n"
                        "C2,BLUE HARBOUR ICE,000000011111\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(FakeSite(), "--db", self.db, "match", str(book), "--out", str(out))
        self.assertIn("1 of 2 customers on the list", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        self.assertEqual((rows["C1"]["mwani_authorized"], rows["C1"]["mwani_licences"],
                          rows["C1"]["mwani_licence_types"]), ("1", "1", "Waste Collection and Transportation"))
        self.assertEqual((rows["C2"]["mwani_authorized"], rows["C2"]["mwani_licences"]), ("0", ""))

    def test_a_withdrawn_company_is_kept_as_not_listed(self):
        run(FakeSite(), "--db", self.db, "load")
        smaller = json.dumps({"value": json.loads(LIST)["value"][:2]})
        code, printed = run(FakeSite(smaller), "--db", self.db, "load")
        self.assertIn("2 operators, 1 no longer listed", printed)
        with Store(self.db) as store:
            self.assertEqual(len(store.operators()), 2)

    def test_an_error_answer_fails_the_run_and_keeps_the_list(self):
        run(FakeSite(), "--db", self.db, "load")
        for site in (FakeSite("<html>error</html>"), FakeSite(LIST, status=503), FakeSite('{"value": []}')):
            with self.assertRaises(fetch.SiteError):
                run(site, "--db", self.db, "load")
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs(3)], ["error"] * 3)
            self.assertEqual(len(store.operators()), 3)


if __name__ == "__main__":
    unittest.main()
