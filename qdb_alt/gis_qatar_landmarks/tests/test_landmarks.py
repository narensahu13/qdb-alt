"""Reading the layer's records, and the commands end to end against a stand-in
ArcGIS server. The fixture is a made-up layer in the shape the GIS Qatar
server returns: attributes plus a point in longitude/latitude, sometimes no
point, dates in milliseconds, a company on the map twice."""

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
    from gis_qatar_landmarks import fetch
    from gis_qatar_landmarks.cli import main
    from gis_qatar_landmarks.parse import landmark
    from gis_qatar_landmarks.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from gis_qatar_landmarks import fetch
    from gis_qatar_landmarks.cli import main
    from gis_qatar_landmarks.parse import landmark
    from gis_qatar_landmarks.store import Store

LAYER = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "landmarks.json").read_text(encoding="utf-8"))


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
        assert url == fetch.QUERY_URL and headers["User-Agent"].startswith("qdb-alt/gis_qatar_landmarks")
        self.requests.append(dict(params))
        if params.get("returnCountOnly") == "true":
            n = len(self.features) if self.count is None else self.count
            return Response(self.status, json.dumps({"count": n}))
        assert params["outSR"] == "4326"
        assert not {"PHONE", "EMAIL", "FAX_NO", "POBOX_NO"} & set(params["outFields"].split(","))
        if self.page_body is not None:
            return Response(self.status, self.page_body)
        start = params["resultOffset"]
        page = self.features[start:start + params["resultRecordCount"]]
        return Response(self.status, json.dumps({"features": page}, ensure_ascii=False))


class Rows(unittest.TestCase):
    def test_fields_point_and_date(self):
        a, b, c = (landmark(f) for f in LAYER["features"][:3])
        self.assertEqual((a["id"], a["name"], a["category"], a["district"], a["zone"]),
                         (12, "Northwind Steel Factory", "BUSINESS", "Industrial Area", 57))
        self.assertEqual((a["lat"], a["lon"], a["modified"]), (25.236318, 51.451517, "2022-09-05"))
        self.assertEqual(b["name"], "Northwind Steel - Showroom")
        self.assertIsNone(b["modified"])
        self.assertEqual((c["lat"], c["lon"]), (None, None))                 # no point

    def test_contact_details_are_not_kept(self):
        f = LAYER["features"][0]
        rec = landmark({**f, "attributes": {**f["attributes"], "PHONE": 44000000, "EMAIL": "someone@example.com"}})
        self.assertNotIn("someone@example.com", repr(rec))
        self.assertNotIn("44000000", repr(rec))


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
        fetch.PAGE, fetch.PAUSE = 2, 0          # five records -> pages of 2, 2 and 1

    def tearDown(self):
        fetch.PAGE, fetch.PAUSE = self.saved
        self.tmp.cleanup()

    def test_paging(self):
        site = FakeSite()
        self.assertEqual(run(site, "--db", self.db, "load")[0], 0)
        self.assertEqual([p.get("resultOffset") for p in site.requests], [None, 0, 2, 4])
        with Store(self.db) as store:
            self.assertEqual(len(store.landmarks()), 5)

    def test_load_and_match(self):
        run(FakeSite(), "--db", self.db, "load")
        book = Path(self.tmp.name) / "book.csv"
        book.write_text("customer_id,name,cr_number\n"
                        "C1,NORTHWIND STEEL FACTORY W.L.L,000000011111\n"
                        "C2,NOOR STEEL FACTORY,22222\n"
                        "C3,Sunrise Hotel,33333\n", encoding="utf-8")
        out = Path(self.tmp.name) / "f.csv"
        code, printed = run(FakeSite(), "--db", self.db, "match", str(book), "--out", str(out))
        self.assertIn("2 of 3 customers on the map", printed)
        with open(out, encoding="utf-8-sig") as f:
            rows = {r["customer_id"]: r for r in csv.DictReader(f)}
        c1 = rows["C1"]
        self.assertEqual((c1["gis_landmark"], c1["gis_landmarks"], c1["gis_categories"]), ("1", "2", "BUSINESS"))
        self.assertEqual(c1["gis_districts"], "Abu Hamour; Industrial Area")
        self.assertEqual(c1["matched_landmarks"], "Northwind Steel - Showroom; Northwind Steel Factory")
        # "Abdul Noor Steel Factory" is someone else's brand in front of ours
        self.assertEqual((rows["C2"]["gis_landmark"], rows["C2"]["gis_landmarks"]), ("0", ""))
        self.assertEqual(rows["C3"]["gis_categories"], "LODGING")

    def test_a_place_that_leaves_the_map_is_kept_as_not_listed(self):
        run(FakeSite(), "--db", self.db, "load")
        code, printed = run(FakeSite(LAYER["features"][:4]), "--db", self.db, "load")
        self.assertIn("4 landmarks, 1 no longer listed", printed)
        with Store(self.db) as store:
            self.assertEqual(len(store.landmarks()), 4)
            self.assertIn(("no longer listed", 1), store.stats())

    def test_an_error_answer_fails_the_run_and_keeps_the_layer(self):
        run(FakeSite(), "--db", self.db, "load")
        for site in (FakeSite(page_body='{"error": {"code": 400, "message": "Failed to execute query."}}'),
                     FakeSite(page_body="<html>error</html>"),
                     FakeSite(page_body='{"exceededTransferLimit": false}'),
                     FakeSite(status=503),
                     FakeSite(count=6),                                       # a page went missing
                     FakeSite([])):
            with self.assertRaises(fetch.SiteError):
                run(site, "--db", self.db, "load")
        with Store(self.db) as store:
            self.assertEqual([r["status"] for r in store.runs(6)], ["error"] * 6)
            self.assertEqual(len(store.landmarks()), 5)
        code, printed = run(None, "--db", self.db, "runs", "--limit", "1")
        self.assertIn("error", printed)


if __name__ == "__main__":
    unittest.main()
