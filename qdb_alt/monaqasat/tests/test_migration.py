"""Databases written by earlier versions open, upgrade, and keep working.

The two schemas in fixtures/ are the ones real databases were written
with: the standalone v0.3 package, and the first qdb-alt commit.
"""

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from monaqasat import cli
from monaqasat.fetch import Fetcher
from monaqasat.store import SCHEMA_VERSION, Store

try:
    from test_incremental import FakeSite, _args
except ImportError:                                   # run as tests.test_...
    from tests.test_incremental import FakeSite, _args

FIX = Path(__file__).resolve().parent.parent / "fixtures"
NOW = "2026-09-20T10:00:00+00:00"


def _old_db(path: Path, schema: str, repo_version: bool) -> None:
    """A small database as the old code would have left it."""
    c = sqlite3.connect(path)
    c.executescript((FIX / schema).read_text(encoding="utf-8"))
    c.executemany(
        "INSERT INTO tender (tender_id, family, state, awarded_date,"
        " parsed_at) VALUES (?,?,?,?,?)",
        [("1001", "tender", "awarded", "2026-01-01", NOW),
         ("1002", "tender", "published", None, NOW),
         ("1003", "bid", "awarded", "2026-02-01", NOW)])
    stage = ", stage" if repo_version else ""
    c.executemany(
        f"INSERT INTO company (tender_id, role, seq, name, name_normalised,"
        f" cr_number, cr_root{stage}) VALUES (?,?,?,?,?,?,?"
        f"{',?' if repo_version else ''})",
        [("1001", "awarded", 0, "قطر للوقود (وقود)", "", "24872", "24872",
          "awarded")[: 8 if repo_version else 7],
         ("1001", "bidder", 0, "QATAR PRESS", "qatar press", "030163", "030163",
          "bidder")[: 8 if repo_version else 7]])
    c.execute("INSERT INTO classified_company (profile_number, name,"
              " name_normalised, cr_number, cr_root) VALUES (?,?,?,?,?)",
              ("CP-1", "قطر للوقود", "", "24872", "24872"))
    # the sighting backfill of the time keyed Available tenders as
    # 'published' and bid awards as plain 'awarded'
    c.executemany(
        "INSERT INTO sighting (tender_id, kind, family, state, first_seen,"
        " last_seen) VALUES (?,?,?,?,?,?)",
        [("1001", "awarded", "tender", "awarded", NOW, NOW),
         ("1002", "published", "tender", "published", NOW, NOW),
         ("1003", "awarded", "bid", "awarded", NOW, NOW)])
    c.execute("INSERT INTO detail_fetch VALUES ('1001', 'companies',"
              " 'awarded', ?)", (NOW,))
    c.execute("INSERT INTO detail_fetch VALUES ('1001', 'tender_details',"
              " 'awarded', ?)", (NOW,))
    c.executemany(
        "INSERT INTO failed_page (kind, ref, url, error, attempts, failed_at)"
        " VALUES (?,?,?,?,?,?)",
        [("awarded", "7", "u", "timeout", 1, NOW),
         ("awarded:companies", "1002", "u", "not a tender page", 5, NOW)])
    c.execute("INSERT INTO section_state (kind, reached_end, backfill_complete,"
              " deepest_page, last_page_seen) VALUES ('awarded', 1, 1, 1, 1)")
    c.commit()
    c.close()


class Upgrade:
    schema = ""
    repo_version = False

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "old.db"
        _old_db(self.db, self.schema, self.repo_version)
        self.s = Store(str(self.db))

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def q(self, sql, *a):
        return self.s.conn.execute(sql, a).fetchall()

    def test_version_is_recorded(self):
        self.assertEqual(self.q("PRAGMA user_version")[0][0], SCHEMA_VERSION)

    def test_new_columns_exist(self):
        cols = {r[1] for r in self.q("PRAGMA table_info(sighting)")}
        self.assertTrue({"gone_at", "missed", "last_run"} <= cols)
        cols = {r[1] for r in self.q("PRAGMA table_info(section_state)")}
        self.assertTrue({"last_full_pass", "roll_next", "pass_id"} <= cols)
        cols = {r[1] for r in self.q("PRAGMA table_info(classified_company)")}
        self.assertTrue({"last_pass", "missed", "delisted_at"} <= cols)

    def test_arabic_names_get_a_key(self):
        self.assertEqual(
            self.q("SELECT name_normalised FROM company WHERE role='awarded'"
                   )[0][0], "قطر وقود وقود")
        self.assertEqual(
            self.q("SELECT name_normalised FROM classified_company")[0][0],
            "قطر وقود")

    def test_cr_roots_are_recomputed(self):
        self.assertEqual(
            self.q("SELECT cr_root FROM company WHERE role='bidder'")[0][0],
            "30163")

    def test_stage_values_are_valid(self):
        self.assertEqual({r[0] for r in self.q("SELECT stage FROM company")},
                         {"awarded", "unknown"})

    def test_sightings_are_rekeyed_to_real_sections(self):
        kinds = {(r[0], r[1]) for r in self.q(
            "SELECT tender_id, kind FROM sighting")}
        self.assertEqual(kinds, {("1001", "awarded"), ("1002", "available"),
                                 ("1003", "bids-awarded")})

    def test_old_detail_failures_are_dropped_listing_ones_kept(self):
        self.assertEqual([tuple(r) for r in self.q(
            "SELECT kind, ref FROM failed_page")], [("awarded", "7")])

    def test_opening_twice_changes_nothing(self):
        before = self.q("SELECT * FROM sighting ORDER BY 1, 2")
        self.s.close()
        self.s = Store(str(self.db))
        self.assertEqual(self.q("SELECT * FROM sighting ORDER BY 1, 2"), before)

    def test_a_crawl_on_it_does_not_refetch_what_it_holds(self):
        site = FakeSite()
        site.sections["AwardedTenders"] = ["1001"]
        self.s.close()
        with mock.patch.object(Fetcher, "check_robots", lambda self: ""), \
                mock.patch.object(Fetcher, "get",
                                  lambda self, rel: site.get(rel)), \
                mock.patch("builtins.print"):
            cli.cmd_crawl(_args(db=str(self.db), kind="awarded"))
        self.s = Store(str(self.db))
        self.assertEqual(site.count("TenderCompaniesDetails/1001"), 0)
        self.assertEqual(site.count("TenderDetails/1001"), 0)
        # an old database has never had a rolling pass: one starts
        self.assertIsNotNone(self.s.section("awarded")["last_full_pass"])


class FromV03(Upgrade, unittest.TestCase):
    schema = "schema_v0.3.sql"
    repo_version = False


class FromFirstQdbAlt(Upgrade, unittest.TestCase):
    schema = "schema_qdb-alt_2026-09-21.sql"
    repo_version = True


if __name__ == "__main__":
    unittest.main()
