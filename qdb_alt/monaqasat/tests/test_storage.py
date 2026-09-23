"""How pages are stored: compressed, readable back, and safe to share.

The database is nearly all stored HTML. These tests cover the compression
(v3), reading back pages written by older versions, compacting an existing
database, and what a packed copy is allowed to contain.
"""

import contextlib
import io
import sqlite3
import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

from monaqasat import cli
from monaqasat.store import (DICT_MIN, SCHEMA_VERSION, Store, compress_html,
                             decompress_html, pack_database)

ARABIC = "قطر للوقود (وقود)"
CHROME = ("<html><head><title>Monaqasat</title>"
          "<script src='/js/dynatrace.js'></script>"
          "<link rel='stylesheet' href='/css/site.css'></head><body>"
          "<nav class='navbar'>"
          + "".join(f"<a href='/menu/{i}'>Tenders online services {i}</a>"
                    for i in range(400))
          + "</nav>")
FOOT = ("<footer>" + "".join(f"<span>Ministry of Finance, Doha, Qatar {i}</span>"
                             for i in range(250))
        + "</footer></body></html>")


def page(n: int, extra: str = "") -> str:
    """A page like the site's: a lot of shared chrome, a little content."""
    return (CHROME + f"<div class='content'><h3>Tender {n}</h3>{extra}"
            "<table class='custom--table'><tr><td>CO</td>"
            f"<td>{n} | 1</td><td>1,000.00 QAR</td></tr></table></div>" + FOOT)


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_page_reads_back_exactly_as_fetched(self):
        store = Store(self.db)
        for i in range(3):
            store.save_page(f"https://x/d/{i}", "detail", page(i),
                            tender_id=str(i))
        rows = {r["url"]: r for r in store.pages("detail")}
        for i in range(3):
            self.assertEqual(store.html_of(rows[f"https://x/d/{i}"]), page(i))
        store.close()

    def test_arabic_survives(self):
        store = Store(self.db)
        store.save_page("https://x/d/ar", "detail", page(1, ARABIC),
                        tender_id="ar")
        row = next(iter(store.pages("detail")))
        self.assertIn(ARABIC, store.html_of(row))
        store.close()

    def test_nothing_is_left_in_the_html_column(self):
        store = Store(self.db)
        store.save_page("https://x/d/1", "detail", page(1), tender_id="1")
        row = store.conn.execute("SELECT html, body, comp FROM raw_page"
                                 ).fetchone()
        self.assertIsNone(row["html"])
        self.assertTrue(row["comp"])
        self.assertIsInstance(row["body"], bytes)
        store.close()

    def test_pages_of_one_kind_share_a_sample(self):
        store = Store(self.db)
        for i in range(6):
            store.save_page(f"https://x/d/{i}", "detail", page(i),
                            tender_id=str(i))
        dicts = store.conn.execute(
            "SELECT kind, COUNT(*) n FROM blob_dict GROUP BY kind").fetchall()
        self.assertEqual([tuple(r) for r in dicts], [("detail", 1)])
        store.close()

    def test_the_sample_makes_pages_much_smaller(self):
        store = Store(self.db)
        for i in range(6):
            store.save_page(f"https://x/d/{i}", "detail", page(i),
                            tender_id=str(i))
        raw = sum(len(page(i).encode()) for i in range(6))
        stored = store.conn.execute(
            "SELECT SUM(length(body)) FROM raw_page").fetchone()[0]
        self.assertLess(stored * 8, raw, f"{raw} -> {stored} bytes")
        store.close()

    def test_a_tiny_page_still_round_trips(self):
        store = Store(self.db)
        tiny = "<html>too small to learn from</html>"
        self.assertLess(len(tiny), DICT_MIN)
        store.save_page("https://x/l/1", "listing", tiny, page=1)
        row = next(iter(store.pages("listing")))
        self.assertEqual(store.html_of(row), tiny)
        store.close()

    def test_reopening_reads_pages_written_before(self):
        store = Store(self.db)
        store.save_page("https://x/d/1", "detail", page(1), tender_id="1")
        store.close()
        store = Store(self.db)                 # fresh dictionary cache
        row = next(iter(store.pages("detail")))
        self.assertEqual(store.html_of(row), page(1))
        store.close()

    def test_unchanged_and_changed_still_work(self):
        store = Store(self.db)
        url = "https://x/d/1"
        self.assertEqual(store.save_page(url, "detail", page(1)), "new")
        self.assertEqual(store.save_page(url, "detail", page(1)), "unchanged")
        self.assertEqual(store.save_page(url, "detail", page(2)), "changed")
        row = next(iter(store.pages("detail")))
        self.assertEqual(store.html_of(row), page(2))
        self.assertTrue(store.has_page(url))
        store.close()

    def test_a_missing_sample_is_reported_not_silently_wrong(self):
        body, comp = compress_html(page(1), b"x" * 5000, 7)
        with self.assertRaises(ValueError):
            decompress_html(body, comp, None)


class OldDatabases(unittest.TestCase):
    """A database written before v3 keeps working, and `compact` shrinks it."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "old.db")
        store = Store(self.db)
        store.close()
        c = sqlite3.connect(self.db)
        for i in range(8):
            c.execute(
                "INSERT INTO raw_page (url, kind, tender_id, fetched_at,"
                " first_seen_at, content_sha256, html)"
                " VALUES (?,?,?,?,?,?,?)",
                (f"https://x/d/{i}", "detail", str(i),
                 f"2026-0{i % 9 + 1}-01T00:00:00+00:00",
                 "2026-01-01T00:00:00+00:00", f"sha{i}", page(i)))
        c.execute("PRAGMA user_version = 2")
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_pages_are_still_readable(self):
        store = Store(self.db)
        rows = list(store.pages("detail"))
        self.assertEqual(len(rows), 8)
        self.assertEqual(store.html_of(rows[0]), page(0))
        self.assertTrue(store.has_page("https://x/d/0"))
        store.close()

    def test_opening_does_not_rewrite_anything(self):
        store = Store(self.db)
        self.assertEqual(store.conn.execute("PRAGMA user_version").fetchone()[0],
                         SCHEMA_VERSION)
        left = store.conn.execute(
            "SELECT COUNT(*) FROM raw_page WHERE html IS NOT NULL").fetchone()[0]
        self.assertEqual(left, 8, "compacting is a command, not a surprise")
        store.close()

    def test_compact_compresses_and_keeps_every_page(self):
        store = Store(self.db)
        before = {r["url"]: store.html_of(r) for r in store.pages("detail")}
        result = store.compact()
        self.assertEqual(result["compressed"], 8)
        self.assertLess(result["bytes after"] * 4, result["bytes before"])
        after = {r["url"]: store.html_of(r) for r in store.pages("detail")}
        self.assertEqual(after, before)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM raw_page WHERE html IS NOT NULL"
        ).fetchone()[0], 0)
        self.assertEqual(store.compact()["compressed"], 0)
        store.close()

    def test_compact_can_drop_old_pages_but_keeps_their_rows(self):
        store = Store(self.db)
        result = store.compact(drop_before="2026-03-01")
        self.assertEqual(result["dropped"], 2)
        rows = {r["url"]: store.html_of(r) for r in store.pages("detail")}
        self.assertEqual(len(rows), 8)
        self.assertIsNone(rows["https://x/d/0"])
        self.assertEqual(rows["https://x/d/7"], page(7))
        self.assertEqual(store.conn.execute(
            "SELECT content_sha256 FROM raw_page WHERE url='https://x/d/0'"
        ).fetchone()[0], "sha0", "provenance stays")
        store.close()

    def test_re_parsing_works_after_compacting(self):
        store = Store(self.db)
        store.compact()
        store.close()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_parse(Namespace(db=self.db))  # reads every stored page
        store = Store(self.db)
        self.assertEqual(store.stats()["detail pages stored"], 8)
        store.close()


class PackedCopy(unittest.TestCase):
    def test_pages_samples_and_customer_matches_are_left_out(self):
        with TemporaryDirectory() as tmp:
            src = str(Path(tmp) / "full.db")
            store = Store(src)
            store.upsert_tender({"tender_id": "1", "family": "tender",
                                 "state": "awarded"})
            store.save_page("https://x/d/1", "detail", page(1), tender_id="1")
            store.save_matches([{
                "customer_id": "CIF-001", "tender_id": "1", "role": "awarded",
                "seq": 0, "method": "cr", "score": 1.0,
                "customer_name": "A BORROWER WLL", "matched_name": "CO 1",
                "matched_cr": "29309"}])
            store.commit()
            store.close()

            dest = str(Path(tmp) / "slim.db")
            pack_database(src, dest)
            c = sqlite3.connect(dest)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tender"
                                       ).fetchone()[0], 1)
            for table in ("raw_page", "blob_dict", "match"):
                self.assertEqual(
                    c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0,
                    f"{table} must not travel in a packed copy")
            dump = "\n".join(c.iterdump())
            self.assertNotIn("CIF-001", dump)
            self.assertNotIn("A BORROWER WLL", dump)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],
                             SCHEMA_VERSION)
            c.close()


if __name__ == "__main__":
    unittest.main()
