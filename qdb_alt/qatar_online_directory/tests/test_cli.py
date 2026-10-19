"""The commands end to end, offline: a fake site serves the fixtures, `search`
reads it, `match` writes the features, the run log records both.

The fake site answers the five customers of the book in five different ways:
found with two listings, found on the second result page, not listed, the
connection fails, and a block page comes back instead of results."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

try:
    from qatar_online_directory import fetch
    from qatar_online_directory.cli import main
    from qatar_online_directory.store import Store
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_online_directory import fetch
    from qatar_online_directory.cli import main
    from qatar_online_directory.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class Response:
    def __init__(self, text: str, status: int = 200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")


class FakeSite:
    """Routes requests to fixture files; `down` makes every request fail."""

    SEARCHES = {("Sunrise Dates Factory", 1): "search_sunrise.html",
                ("Blue Harbour Ice", 1): "search_blue_1.html",
                ("Blue Harbour Ice", 2): "search_blue_2.html",
                ("Golden Harbour Ice", 1): "search_none.html",
                ("Silver Gulf Trading", 1): "block_page.html"}

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []
        self.down = False

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        assert headers == fetch.UA, headers              # our own User-Agent, never a browser's
        if self.down:
            raise requests.ConnectionError("connection refused")
        if url == fetch.SEARCH:
            q = params["company"]
            if q == "Northwind Plastic Industries":
                raise requests.ConnectionError("connection reset")
            name = self.SEARCHES.get((q, int(params.get("page", 1))))
            return Response((FIXTURES / name).read_text(encoding="utf-8")) if name else Response("", 404)
        listing = url.split("/company-details/")[1].split("/")[0]
        f = FIXTURES / f"company_{listing}.html"
        return Response(f.read_text(encoding="utf-8")) if f.exists() else Response("", 500)


def run(*argv, site=None) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = main(list(argv), session=site)
    return code, out.getvalue()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.out = str(Path(self.tmp.name) / "features.csv")
        self.book = Path(self.tmp.name) / "book.csv"
        self.book.write_text(
            "customer_id,name,cr_number\n"
            "C1,SUNRISE DATES FACTORY W.L.L,000000012345\n"
            "C2,BLUE HARBOUR ICE CO,0000054321\n"
            "C3,GOLDEN HARBOUR ICE,000000011111\n"
            "C4,NORTHWIND PLASTIC INDUSTRIES W.L.L,000000022222\n"
            "C5,SILVER GULF TRADING,000000033333\n",
            encoding="utf-8")
        self.site = FakeSite()

    def tearDown(self):
        self.tmp.cleanup()

    def search(self):
        return run("--db", self.db, "search", str(self.book), "--pause", "0", "--no-system-trust", site=self.site)

    def features(self):
        code, printed = run("--db", self.db, "match", str(self.book), "--out", self.out)
        self.assertEqual(code, 0)
        with open(self.out, encoding="utf-8-sig") as f:
            return printed, {r["customer_id"]: r for r in csv.DictReader(f)}

    def test_search_match_and_the_run_log(self):
        code, _ = self.search()
        self.assertEqual(code, 0)
        printed, rows = self.features()
        self.assertIn("2 of 5 customers found; 2 without a search", printed)

        c1 = rows["C1"]
        self.assertEqual((c1["qod_listed"], c1["qod_listings"], c1["qod_city"], c1["qod_main_category"]),
                         ("1", "2", "Doha", "DATES PROCESSING AND PACKING"))
        self.assertEqual((c1["qod_categories"], c1["qod_brands"], c1["qod_has_po_box"], c1["qod_has_phone"]),
                         ("3", "1", "1", "1"))
        self.assertEqual(c1["matched_name"], "Sunrise Dates Factory WLL")
        self.assertEqual(c1["listing_url"].split(), [
            "https://qataronlinedirectory.com/company-details/9002/Sunrise-Dates-Factory-WLL",
            "https://qataronlinedirectory.com/company-details/9003/Sunrise-Date-Factory"])

        self.assertEqual((rows["C2"]["qod_listed"], rows["C2"]["matched_name"]), ("1", "Blue Harbour Ice & Water"))
        self.assertEqual((rows["C2"]["qod_has_po_box"], rows["C2"]["qod_has_phone"]), ("1", "0"))
        self.assertEqual((rows["C3"]["qod_listed"], rows["C3"]["qod_city"]), ("0", ""))   # not listed: 0, rest empty
        for failed in ("C4", "C5"):                                                         # no answer: all empty
            self.assertEqual({k: v for k, v in rows[failed].items() if k.startswith("qod_") and v}, {})

        with Store(self.db) as store:
            log = store.runs()
        self.assertEqual([r["command"] for r in log], ["match", "search 5"])
        self.assertTrue(all(r["status"] == "ok" and r["finished_at"] and r["seconds"] is not None for r in log))
        self.assertIn("2 searches failed", log[1]["note"])
        self.assertIn("3 listing pages read, 0 failed", log[1]["note"])
        self.assertEqual(log[1]["rows"], 8)                # 5 searches + 3 listing pages
        self.assertEqual(log[0]["note"], "2 of 5 customers found; 2 without a search (not searched yet or failed)")

    def test_a_second_search_asks_only_for_what_failed(self):
        self.search()
        before = len(self.site.calls)
        self.search()
        asked = [p["company"] for _, p in self.site.calls[before:]]
        self.assertEqual(sorted(asked), ["Northwind Plastic Industries", "Silver Gulf Trading"])

    def test_a_failure_never_replaces_an_earlier_answer(self):
        self.search()
        self.site.down = True
        code, _ = run("--db", self.db, "search", str(self.book), "--pause", "0", "--no-system-trust",
                      "--max-age-days", "0", site=self.site)
        self.assertEqual(code, 1)                          # five failures in a row: it stops asking
        with Store(self.db) as store:
            (last,) = store.runs(1)
            self.assertEqual(store.search("Sunrise Dates Factory")["status"], "ok")
        self.assertEqual(last["status"], "error")
        self.assertIn("SiteUnavailable", last["error"])
        self.assertIn("searches failed", last["note"])
        _, rows = self.features()
        self.assertEqual(rows["C1"]["qod_listed"], "1")

    def test_match_before_search_gives_empty_rows(self):
        printed, rows = self.features()
        self.assertIn("0 of 5 customers found; 5 without a search", printed)
        self.assertEqual({r["qod_listed"] for r in rows.values()}, {""})


if __name__ == "__main__":
    unittest.main()
