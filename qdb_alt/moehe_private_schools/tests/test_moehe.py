"""Reading the layer rows, and the commands end to end against a stand-in
ArcGIS server. The fixture is a made-up layer in the shape the ministry's
server returns: names with stray and non-breaking spaces, stages joined with
"+", curricula in Arabic, the same school repeated for a second building."""

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
    from moehe_private_schools import fetch
    from moehe_private_schools.cli import main
    from moehe_private_schools.match import brand, is_school
    from moehe_private_schools.parse import curricula, levels, school
    from moehe_private_schools.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from moehe_private_schools import fetch
    from moehe_private_schools.cli import main
    from moehe_private_schools.match import brand, is_school
    from moehe_private_schools.parse import curricula, levels, school
    from moehe_private_schools.store import Store

LAYER = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "schools.json").read_text(encoding="utf-8"))
ROWS = [f["attributes"] for f in LAYER["features"]]


class Response:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSite:
    """Answers the two ArcGIS queries the loader makes: the record count, and a page of records."""

    def __init__(self, features=None, status=200, count=None, page_body=None):
        self.features = LAYER["features"] if features is None else features
        self.status, self.count, self.page_body = status, count, page_body
        self.requests = []

    def get(self, url, params=None, headers=None, timeout=None):
        assert url == fetch.QUERY_URL and headers["User-Agent"].startswith("qdb-alt/moehe_private_schools")
        self.requests.append(dict(params))
        if params.get("returnCountOnly") == "true":
            n = len(self.features) if self.count is None else self.count
            return Response(self.status, json.dumps({"count": n}))
        assert "PHONE" not in params["outFields"] and "EMAIL" not in params["outFields"]
        if self.page_body is not None:
            return Response(self.status, self.page_body)
        start = params["resultOffset"]
        page = self.features[start:start + params["resultRecordCount"]]
        return Response(self.status, json.dumps({"features": page}, ensure_ascii=False))


class Rows(unittest.TestCase):
    def test_names_stages_and_curricula(self):
        a, b, c = school(ROWS[0]), school(ROWS[1]), school(ROWS[2])
        self.assertEqual(a["name"], b["name"])                             # trailing space dropped
        self.assertEqual(b["name_ar"], a["name_ar"])                       # line break dropped
        self.assertEqual(c["name"], "Sunrise International School- Al Wakra")
        self.assertEqual(a["levels"], ["Kindergarten", "Primary"])
        self.assertEqual(c["levels"], ["Preparatory", "Secondary"])
        self.assertEqual(c["curricula"], ["British", "American"])
        self.assertEqual(c["status"], "Temporarily Closed")

    def test_unknown_text_is_kept_as_published(self):
        rec = school(ROWS[5])
        self.assertEqual((rec["levels"], rec["curricula"], rec["status"]), (["Nursery"], ["منهج خاص"],
                                                                            "Permanently Closed"))
        self.assertEqual(curricula("IB"), ["IB"])
        self.assertEqual(curricula(None), [])
        self.assertEqual(levels(" Primary + Preparatory\u00a0"), ["Primary", "Preparatory"])

    def test_contact_details_are_not_kept(self):
        rec = school({**ROWS[0], "PHONE": 44000000, "EMAIL": "someone@example.com"})
        self.assertNotIn("someone@example.com", repr(rec))
        self.assertNotIn("44000000", repr(rec))


class Names(unittest.TestCase):
    def test_which_customers_are_schools(self):
        self.assertTrue(is_school({"name": "Baraem Alghad Kindergaten"}))         # misspelt
        self.assertTrue(is_school({"name": "Almesnid Educational Center"}))
        self.assertFalse(is_school({"name": "Qatar Steel Industries Factory"}))

    def test_brand(self):
        self.assertEqual(brand("Al Hammad International Developed School"), "HAMMAD")
        self.assertEqual(brand("Alhammad School"), "HAMMAD")
        self.assertEqual(brand("Newton International Kindergarten - Oryx"), "NEWTON")
        self.assertIsNone(brand("Doha Academy"))


def run(site, *argv):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(list(argv), session=site)
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.saved = fetch.PAGE, fetch.PAUSE
        fetch.PAGE, fetch.PAUSE = 2, 0          # six records -> three pages and an empty fourth

    def tearDown(self):
        fetch.PAGE, fetch.PAUSE = self.saved
        self.tmp.cleanup()

    def test_paging(self):
        site = FakeSite()
        self.assertEqual(run(site, "--db", self.db, "load")[0], 0)
        self.assertEqual([p.get("resultOffset") for p in site.requests], [None, 0, 2, 4, 6])
        with Store(self.db) as store:
            self.assertEqual(len(store.schools()), 6)
            self.assertIn(("schools (name + district)", 5), store.stats())

    def test_load_and_match(self):
        run(FakeSite(), "--db", self.db, "load")
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,SUNRISE INTERNATIONAL SCHOOL W.L.L,000000011111\n"
                        "C2,ORYX KIDS NURSERY,22222\n"
                        "C3,NORTHWIND STEEL FACTORY,33333\n"
                        "C4,Bright Future Academy,44444\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(FakeSite(), "--db", self.db, "match", str(book), "--out", str(out))
        self.assertIn("2 of 3 school customers registered (4 customers)", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        c1 = rows["C1"]
        self.assertEqual((c1["school_registered"], c1["school_campuses"], c1["school_open"]), ("1", "2", "1"))
        self.assertEqual(c1["school_levels"], "Kindergarten; Primary; Preparatory; Secondary")
        self.assertEqual(c1["school_curricula"], "British; American")
        self.assertEqual(c1["school_districts"], "Al Waab; Al Wakra")
        # Oryx is the campus of a Newton kindergarten, not a school called Oryx
        self.assertEqual((rows["C2"]["school_registered"], rows["C2"]["school_campuses"]), ("0", ""))
        # not a school: nothing to say, not "0"
        self.assertEqual({k: v for k, v in rows["C3"].items() if k.startswith(("school", "matched"))},
                         dict.fromkeys(["school_registered", "school_campuses", "school_open", "school_levels",
                                        "school_curricula", "school_districts", "matched_schools"], ""))
        self.assertEqual((rows["C4"]["school_curricula"], rows["C4"]["matched_schools"]),
                         ("Indian", "Bright Future Academy"))
        with Store(self.db) as store:
            self.assertIn(("customers with a school name", 3), store.stats())

    def test_a_school_that_leaves_the_layer_is_kept_as_not_listed(self):
        run(FakeSite(), "--db", self.db, "load")
        code, printed = run(FakeSite(LAYER["features"][:5]), "--db", self.db, "load")
        self.assertIn("5 school records, 1 no longer listed", printed)
        with Store(self.db) as store:
            self.assertEqual(len(store.schools()), 5)

    def test_an_error_answer_fails_the_run_and_keeps_the_layer(self):
        run(FakeSite(), "--db", self.db, "load")
        for site in (FakeSite(page_body='{"error": {"code": 400, "message": "Failed to execute query."}}'),
                     FakeSite(page_body="<html>error</html>"),
                     FakeSite(page_body='{"exceededTransferLimit": false}'),
                     FakeSite(status=503),
                     FakeSite(count=7),                                       # a page went missing
                     FakeSite([])):
            with self.assertRaises(fetch.SiteError):
                run(site, "--db", self.db, "load")
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs(6)], ["error"] * 6)
            self.assertEqual(len(store.schools()), 6)
        code, printed = run(None, "--db", self.db, "runs", "--limit", "1")
        self.assertIn("error", printed)


if __name__ == "__main__":
    unittest.main()
