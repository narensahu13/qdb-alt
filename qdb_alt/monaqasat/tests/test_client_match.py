"""A client, its buyers and its suppliers -- and what the model gets.

Two things are being checked here. First, that a match carries the facts a
credit model needs: when the tender closed, when it was awarded, how much
that company was awarded, and who awarded it. The first version of the
export carried none of that, only that a match existed.

Second, that a counterparty lifted from a bank narration is never treated
like a borrower whose CR we hold. It has no registration number, its name is
abbreviated, and a wrong join there puts a stranger's contract history on a
borrower's file.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from monaqasat.features import build_client_months
    from monaqasat.match import (load_counterparties, match_parties, parties,
                                 _direction)
    from monaqasat.store import Store
except ImportError:                                    # run from tests/
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from monaqasat.features import build_client_months
    from monaqasat.match import (load_counterparties, match_parties, parties,
                                 _direction)
    from monaqasat.store import Store


def _store(tmp: str) -> Store:
    """A small register: one borrower, one of its buyers, one supplier, and
    an unrelated company whose name is close to the supplier's."""
    s = Store(str(Path(tmp) / "t.db"))
    tenders = [
        # tender_id, number, ministry, state, closing, awarded, amount
        ("T1", "2024/1", "Ministry of Public Health", "awarded",
         "2025-01-10", "2025-03-01", 1_000_000.0),
        ("T2", "2024/2", "Ashghal", "awarded",
         "2025-05-10", "2025-06-01", 500_000.0),
        ("T3", "2024/3", "Ministry of Public Health", "closed",
         "2026-02-10", None, None),
        ("T4", "2024/4", "Kahramaa", "awarded",
         "2026-01-05", "2026-02-15", 250_000.0),
    ]
    for tid, num, ministry, state, closing, awarded, amount in tenders:
        s.conn.execute(
            "INSERT INTO tender (tender_id, tender_number, ministry, state,"
            " closing_date, awarded_date, awarded_amount, family,"
            " local_value_system, subject, sector_type)"
            " VALUES (?,?,?,?,?,?,?, 'tender', 'ICV required', 'work', 'Goods')",
            (tid, num, ministry, state, closing, awarded, amount))

    rows = [
        # tender, role, seq, name, cr, value
        ("T1", "awarded", 0, "BORROWER TRADING W L L", "29309", 1_000_000.0),
        ("T2", "bidder", 0, "BORROWER TRADING W L L", "29309", None),
        ("T2", "awarded", 0, "SOMEONE ELSE", "11111", 500_000.0),
        ("T3", "bidder", 0, "BORROWER TRADING W L L", "29309", None),
        ("T4", "awarded", 0, "GULF STEEL FACTORY", "55555", 250_000.0),
        ("T1", "bidder", 1, "AL WAKRA FOODS", "77777", None),
        # close to "GULF STEEL FACTORY" but a different company
        ("T2", "bidder", 1, "GULF STEEL FACTORIES", "88888", None),
    ]
    from monaqasat.normalize import cr_root, normalize_name
    for tid, role, seq, name, cr, value in rows:
        s.conn.execute(
            "INSERT INTO company (tender_id, role, seq, name,"
            " name_normalised, cr_number, cr_root, value, stage,"
            " local_value_ratio)"
            " VALUES (?,?,?,?,?,?,?,?, 'awarded', '35%')",
            (tid, role, seq, name, normalize_name(name), cr, cr_root(cr),
             value))
    s.conn.commit()
    return s


BOOK = [{"customer_id": "CIF-1", "name": "BORROWER TRADING W L L",
         "cr_number": "29309"}]

COUNTERPARTIES = [
    {"client_id": "CIF-1", "name": "GULF STEEL FACTORY",
     "direction": "supplier", "rank": 1},
    {"client_id": "CIF-1", "name": "AL WAKRA FOODS",
     "direction": "buyer", "rank": 1},
]


def _matched(store):
    rows = match_parties(parties(BOOK, COUNTERPARTIES), store.companies())
    store.save_matches(rows)
    return rows


class WhatTheExportCarries(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = _store(self.tmp.name)
        self.addCleanup(self.s.close)
        _matched(self.s)
        self.rows = {(r["relation"], r["tender_id"], r["role"]): r
                     for r in self.s.match_rows()}

    def test_the_award_carries_its_date_and_its_amount(self):
        # This is the whole complaint about the first version: a match that
        # says only "matched" is not something a model can use.
        r = self.rows[("self", "T1", "awarded")]
        self.assertEqual(r["awarded_date"], "2025-03-01")
        self.assertEqual(r["closing_date"], "2025-01-10")
        self.assertEqual(r["company_value"], 1_000_000.0)
        self.assertEqual(r["buyer"], "Ministry of Public Health")
        self.assertEqual(r["won"], 1)
        self.assertEqual(r["event_date"], "2025-03-01")
        self.assertEqual(r["event_date_basis"], "awarded")

    def test_a_bid_that_lost_is_dated_by_its_closing(self):
        r = self.rows[("self", "T2", "bidder")]
        self.assertEqual(r["won"], 0)
        self.assertEqual(r["event_date"], "2025-06-01")   # the tender's award
        self.assertTrue(r["decided"])

    def test_a_bid_still_under_evaluation_is_neither_won_nor_decided(self):
        r = self.rows[("self", "T3", "bidder")]
        self.assertEqual(r["won"], 0)
        self.assertFalse(r["decided"])
        self.assertEqual(r["event_date_basis"], "closing")

    def test_the_icv_requirement_and_ratio_travel_with_the_row(self):
        r = self.rows[("self", "T1", "awarded")]
        self.assertEqual(r["local_value_system"], "ICV required")
        self.assertEqual(r["local_value_ratio"], "35%")

    def test_competition_on_the_tender_is_countable(self):
        self.assertEqual(self.rows[("self", "T1", "awarded")]
                         ["companies_on_tender"], 2)


class WhoTheMatchIsAbout(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = _store(self.tmp.name)
        self.addCleanup(self.s.close)
        self.rows = _matched(self.s)

    def test_the_client_its_buyer_and_its_supplier_are_told_apart(self):
        got = {(r["relation"], r["matched_name"]) for r in self.rows}
        self.assertIn(("self", "BORROWER TRADING W L L"), got)
        self.assertIn(("supplier", "GULF STEEL FACTORY"), got)
        self.assertIn(("buyer", "AL WAKRA FOODS"), got)

    def test_every_row_says_which_client_it_belongs_to(self):
        self.assertEqual({r["client_id"] for r in self.rows}, {"CIF-1"})

    def test_the_borrower_matches_on_its_cr_and_is_not_flagged(self):
        own = [r for r in self.rows if r["relation"] == "self"]
        self.assertTrue(own)
        self.assertTrue(all(r["method"] == "cr" for r in own))
        self.assertTrue(all(r["needs_review"] == 0 for r in own))

    def test_a_counterparty_is_always_flagged_even_on_an_exact_name(self):
        # It matched exactly, but the name came out of a narration and there
        # is no CR behind it. It is a lead, not a fact.
        cp = [r for r in self.rows if r["relation"] != "self"]
        self.assertTrue(cp)
        self.assertTrue(all(r["method"] == "name_exact" for r in cp))
        self.assertTrue(all(r["needs_review"] == 1 for r in cp))

    def test_the_rank_from_the_transaction_model_is_kept(self):
        cp = [r for r in self.rows if r["relation"] == "supplier"]
        self.assertEqual({r["party_rank"] for r in cp}, {1})

    def test_a_near_name_joins_for_the_book_but_not_from_a_narration(self):
        # "AL WAKRA FOOD" against "AL WAKRA FOODS" scores 0.95: over the bar
        # for a borrower whose file we can open and check, under the bar for
        # a name lifted off a bank statement, where a wrong join puts a
        # stranger's contract history on the borrower.
        near = "AL WAKRA FOOD"
        as_book = match_parties(
            parties([{"customer_id": "X", "name": near}], []),
            self.s.companies())
        as_txn = match_parties(
            parties([], [{"client_id": "X", "name": near,
                          "direction": "buyer"}]),
            self.s.companies())
        self.assertIn("AL WAKRA FOODS", {r["matched_name"] for r in as_book})
        self.assertEqual([r["method"] for r in as_book], ["name_fuzzy"])
        self.assertEqual(as_txn, [])

    def test_a_live_counterparty_run_does_not_wipe_the_stored_book_matches(self):
        before = self.s.conn.execute(
            "SELECT COUNT(*) FROM match WHERE relation='self'").fetchone()[0]
        self.s.clear_matches(["CIF-1"], ["buyer", "supplier"])
        after = self.s.conn.execute(
            "SELECT COUNT(*) FROM match WHERE relation='self'").fetchone()[0]
        cp = self.s.conn.execute(
            "SELECT COUNT(*) FROM match WHERE relation<>'self'").fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(cp, 0)


class Direction(unittest.TestCase):
    def test_the_words_a_transaction_model_uses(self):
        for word in ("buyer", "Buyers", "customer", "receivables", "sales"):
            self.assertEqual(_direction(word), "buyer", word)
        for word in ("supplier", "Vendors", "payables", "purchases"):
            self.assertEqual(_direction(word), "supplier", word)

    def test_an_unknown_word_is_never_guessed_into_a_side(self):
        # A supplier counted as a customer inverts the signal, so an
        # unrecognised label stays unrecognised.
        for word in ("", None, "counterparty", "misc", "other"):
            self.assertEqual(_direction(word), "counterparty", repr(word))

    def test_a_list_without_a_direction_column_still_loads(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "cp.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["CIF No", "Counterparty Name", "Top N"])
                w.writerow(["CIF-1", "GULF STEEL FACTORY", "1"])
            rows, report = load_counterparties(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_id"], "CIF-1")
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["direction"], "counterparty")
        self.assertTrue(report["notes"])

    def test_a_list_with_no_name_column_says_so(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "cp.csv"
            p.write_text("client_id,amount\nCIF-1,100\n", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_counterparties(p)
        self.assertIn("name", str(caught.exception))


class ClientMonths(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = _store(self.tmp.name)
        self.addCleanup(self.s.close)
        _matched(self.s)
        build_client_months(self.s, as_of_end=date(2026, 6, 30))
        self.months = {r["as_of_month"]: r for r in self.s.conn.execute(
            "SELECT * FROM client_month WHERE client_id='CIF-1'")}

    def test_a_month_does_not_know_about_a_later_contract(self):
        # The point of the whole table: scoring a 2025 application must not
        # see the supplier's February 2026 award.
        row = self.months["2025-12-31"]
        self.assertEqual(row["self_wins_12m"], 1)         # its own, March
        self.assertEqual(row["suppliers_active_12m"], 0)
        self.assertIsNone(row["supplier_award_value_12m"])

    def test_the_series_starts_at_the_first_event_not_before(self):
        # Nothing is asserted about a client before there is anything to
        # say about it; an empty row would read as a quiet borrower.
        self.assertEqual(min(self.months), "2025-03-31")

    def test_the_month_of_the_award_counts_it(self):
        row = self.months["2025-03-31"]
        self.assertEqual(row["self_wins_12m"], 1)
        self.assertEqual(row["self_award_value_12m"], 1_000_000.0)
        self.assertEqual(row["self_distinct_buyers_12m"], 1)

    def test_a_win_rate_needs_enough_decided_tenders_to_mean_anything(self):
        row = self.months["2025-06-30"]
        self.assertEqual(row["self_decided_12m"], 2)
        self.assertIsNone(row["self_win_rate_12m"])

    def test_the_counterparties_are_counted_on_their_own_side(self):
        row = self.months["2026-02-28"]
        self.assertEqual(row["suppliers_matched"], 1)
        self.assertEqual(row["suppliers_active_12m"], 1)
        self.assertEqual(row["supplier_award_value_12m"], 250_000.0)
        self.assertEqual(row["buyers_matched"], 1)
        self.assertEqual(row["buyers_active_12m"], 0)   # bid only, no award

    def test_the_counterparty_numbers_say_how_much_rests_on_a_name(self):
        row = self.months["2026-02-28"]
        self.assertEqual(row["counterparty_review_share"], 1.0)

    def test_months_since_the_last_award_keeps_counting(self):
        self.assertEqual(self.months["2025-03-31"]
                         ["self_months_since_last_award"], 0)
        self.assertEqual(self.months["2026-03-31"]
                         ["self_months_since_last_award"], 12)

    def test_the_table_is_rebuilt_not_appended_to(self):
        before = self.s.conn.execute(
            "SELECT COUNT(*) FROM client_month").fetchone()[0]
        build_client_months(self.s, as_of_end=date(2026, 6, 30))
        after = self.s.conn.execute(
            "SELECT COUNT(*) FROM client_month").fetchone()[0]
        self.assertEqual(before, after)


class MigratingAnOlderDatabase(unittest.TestCase):
    def test_matches_written_before_v4_are_carried_over_as_the_client(self):
        with TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.db")
            c = sqlite3.connect(path)
            c.executescript(
                "CREATE TABLE match (customer_id TEXT NOT NULL,"
                " tender_id TEXT NOT NULL, role TEXT NOT NULL,"
                " seq INTEGER NOT NULL, method TEXT NOT NULL, score REAL,"
                " customer_name TEXT, matched_name TEXT, matched_cr TEXT,"
                " matched_at TEXT,"
                " PRIMARY KEY (customer_id, tender_id, role, seq));"
                "INSERT INTO match VALUES ('CIF-9','T1','awarded',0,'cr',1.0,"
                " 'OLD BORROWER','OLD BORROWER','29309','2026-01-01');"
                "INSERT INTO match VALUES ('CIF-9','T2','bidder',0,"
                " 'name_fuzzy',0.95,'OLD BORROWER','OLD BORROWR','29309',"
                " '2026-01-01');"
                "PRAGMA user_version = 3;")
            c.commit()
            c.close()

            s = Store(path)
            try:
                rows = s.conn.execute(
                    "SELECT * FROM match ORDER BY tender_id").fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual({r["client_id"] for r in rows}, {"CIF-9"})
                self.assertEqual({r["relation"] for r in rows}, {"self"})
                self.assertEqual({r["party_source"] for r in rows}, {"book"})
                # the fuzzy one was always a lead, and stays one
                flags = {r["tender_id"]: r["needs_review"] for r in rows}
                self.assertEqual(flags, {"T1": 0, "T2": 1})
            finally:
                s.close()


if __name__ == "__main__":
    unittest.main()
