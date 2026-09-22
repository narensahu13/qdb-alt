"""Matching and the end-to-end path, using the store but never the network."""

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monaqasat.match import load_customers, match_customers, summarise
from monaqasat.parse import parse_detail, parse_listing
from monaqasat.store import Store

FIX = Path(__file__).resolve().parent.parent / "fixtures"
DETAIL = (FIX / "detail_659460.html").read_text(encoding="utf-8")
LISTING = (FIX / "listing_awarded_p1.html").read_text(encoding="utf-8")


def _loaded_store(path):
    """Everything `crawl` would do, minus the HTTP."""
    store = Store(path)
    url_l = "https://x/TendersOnlineServices/AwardedTenders/1"
    store.save_page(url_l, "listing", LISTING, page=1)
    for row in parse_listing(LISTING):
        store.upsert_tender(row, source_url=url_l)
    url_d = "https://x/TendersOnlineServices/TenderCompaniesDetails/659460"
    store.save_page(url_d, "detail", DETAIL, tender_id="659460")
    parsed = parse_detail(DETAIL, "659460")
    store.upsert_tender(parsed["tender"], source_url=url_d)
    store.replace_companies("659460", parsed["companies"])
    store.commit()
    return store


class StoreRoundTrip(unittest.TestCase):
    def test_listing_then_detail_keeps_both_sets_of_fields(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            row = store.conn.execute(
                "SELECT * FROM tender WHERE tender_id='659460'").fetchone()
            # from the listing
            self.assertEqual(row["ministry"], "Ministry of Municipality")
            # from the detail page only
            self.assertEqual(row["entity_tender_number"], "56")
            self.assertEqual(row["awarded_amount"], 1819663.33)
            store.close()

    def test_detail_does_not_null_out_listing_fields(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            # Re-apply a detail parse that has no ministry at all.
            thin = parse_detail("<html></html>", "659460")["tender"]
            store.upsert_tender(thin)
            store.commit()
            row = store.conn.execute(
                "SELECT ministry FROM tender WHERE tender_id='659460'"
            ).fetchone()
            self.assertEqual(row["ministry"], "Ministry of Municipality")
            store.close()

    def test_stats_count_what_was_loaded(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            s = store.stats()
            self.assertEqual(s["tenders"], 3)
            self.assertEqual(s["award rows"], 2)
            self.assertEqual(s["bidder rows"], 2)
            self.assertEqual(s["companies with CR"], 4)
            store.close()


class Matching(unittest.TestCase):
    def _companies(self, tmp):
        store = _loaded_store(str(Path(tmp) / "t.db"))
        rows = store.companies()
        store.close()
        return rows

    def test_cr_number_beats_everything(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "Something Else Entirely",
                  "cr_number": "29309"}], companies)
            self.assertTrue(m)
            self.assertEqual(m[0]["method"], "cr")
            self.assertEqual(m[0]["matched_name"], "NAJM ALFARID TRADING")

    def test_cr_branch_suffix_still_joins(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309/4"}],
                companies)
            self.assertEqual({x["method"] for x in m}, {"cr"})

    def test_exact_name_matches_without_a_cr(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C2", "name": "Najm Alfarid Trading WLL",
                  "cr_number": None}], companies)
            self.assertTrue(m)
            self.assertIn(m[0]["method"], {"name_exact", "name_fuzzy"})

    def test_unrelated_customer_does_not_match(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C3", "name": "Doha Dental Clinic",
                  "cr_number": "88888"}], companies)
            self.assertEqual(m, [])

    def test_one_row_per_role(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309"}],
                companies)
            self.assertEqual({x["role"] for x in m}, {"awarded", "bidder"})

    def test_fuzzy_can_be_switched_off(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C4", "name": "Najm Al Farid Trading Co",
                  "cr_number": None}], companies, fuzzy_threshold=1.0)
            self.assertEqual([x for x in m if x["method"] == "name_fuzzy"], [])

    def test_summary_splits_by_method_and_role(self):
        with TemporaryDirectory() as tmp:
            companies = self._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309"}],
                companies)
            s = summarise(m)
            self.assertEqual(s["customers matched"], 1)
            self.assertEqual(s["by method"]["cr"], 2)
            self.assertEqual(s["awarded"], 1)
            self.assertEqual(s["bid only"], 1)

    def test_matches_persist_and_replace_cleanly(self):
        with TemporaryDirectory() as tmp:
            store = _loaded_store(str(Path(tmp) / "t.db"))
            m = match_customers(
                [{"customer_id": "C1", "name": "x", "cr_number": "29309"}],
                store.companies())
            store.save_matches(m)
            store.save_matches(m)          # idempotent on the primary key
            n = store.conn.execute("SELECT COUNT(*) FROM match").fetchone()[0]
            self.assertEqual(n, 2)
            store.clear_matches()
            n = store.conn.execute("SELECT COUNT(*) FROM match").fetchone()[0]
            self.assertEqual(n, 0)
            store.close()


class MultiWinnerTenders(unittest.TestCase):
    """A tender with two winners must not credit either with the other's
    value. This was a real bug: the profile join fanned out across every
    company row on the tender."""

    def _matches(self, tmp):
        store = _loaded_store(str(Path(tmp) / "t.db"))
        customers = [
            {"customer_id": "NAJM", "name": "x", "cr_number": "29309"},
            {"customer_id": "KYRWY", "name": "y", "cr_number": "99457"},
        ]
        m = match_customers(customers, store.companies())
        store.save_matches(m)
        return store, m

    def test_each_customer_matches_only_its_own_row(self):
        with TemporaryDirectory() as tmp:
            store, m = self._matches(tmp)
            najm = [x for x in m
                    if x["customer_id"] == "NAJM" and x["role"] == "awarded"]
            self.assertEqual(len(najm), 1)
            self.assertEqual(najm[0]["matched_name"], "NAJM ALFARID TRADING")
            store.close()

    def test_awarded_value_is_the_customers_own_line(self):
        with TemporaryDirectory() as tmp:
            store, _ = self._matches(tmp)
            rows = store.conn.execute(
                "SELECT m.customer_id, SUM(c.value) v FROM match m"
                " JOIN company c ON c.tender_id=m.tender_id"
                "   AND c.role=m.role AND c.seq=m.seq"
                " WHERE m.role='awarded' GROUP BY m.customer_id").fetchall()
            got = {r["customer_id"]: round(r["v"], 2) for r in rows}
            self.assertEqual(got, {"NAJM": 1481863.33, "KYRWY": 337800.00})
            store.close()

    def test_the_two_customers_sum_to_the_tender_total(self):
        with TemporaryDirectory() as tmp:
            store, _ = self._matches(tmp)
            total = store.conn.execute(
                "SELECT awarded_amount FROM tender WHERE tender_id='659460'"
            ).fetchone()[0]
            per = store.conn.execute(
                "SELECT SUM(c.value) FROM match m JOIN company c"
                " ON c.tender_id=m.tender_id AND c.role=m.role"
                "   AND c.seq=m.seq WHERE m.role='awarded'").fetchone()[0]
            self.assertAlmostEqual(per, total, places=2)
            store.close()


class CustomerCsv(unittest.TestCase):
    def test_reads_id_name_and_cr(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["customer_id", "name", "cr_number"])
                w.writerow(["C001", "AL RAYYAN TRADING W L L", "104772"])
                w.writerow(["", "NAJM ALFARID TRADING", ""])
            rows = load_customers(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["cr_number"], "104772")
        # a missing id falls back to the name, so nothing is silently dropped
        self.assertEqual(rows[1]["customer_id"], "NAJM ALFARID TRADING")


class ArabicNames(unittest.TestCase):
    """QDB's book is often Arabic; Monaqasat English is a machine
    transliteration. Matching has to survive both, with or without a CR."""

    def test_arabic_customer_matches_latin_site_name(self):
        companies = [{
            "tender_id": "1", "role": "awarded", "seq": 1,
            "name": "AL RAYYAN TRADING WLL", "cr_number": None,
            "cr_root": None, "name_ar": None,
        }]
        m = match_customers(
            [{"customer_id": "C1", "name": "شركة الريان للتجارة",
              "cr_number": None, "name_ar": "شركة الريان للتجارة"}],
            companies)
        self.assertTrue(m)
        self.assertEqual(m[0]["matched_name"], "AL RAYYAN TRADING WLL")
        # Cross-script matches have their own method so they can be
        # reviewed separately from same-script ones.
        self.assertEqual(m[0]["method"], "name_translit")

    def test_name_ar_column_used_when_english_name_differs(self):
        companies = [{
            "tender_id": "1", "role": "awarded", "seq": 1,
            "name": "AL RAYYAN TRADING", "cr_number": None,
            "cr_root": None,
        }]
        m = match_customers(
            [{"customer_id": "C1", "name": "Rayyan Co",
              "cr_number": None, "name_ar": "شركة الريان للتجارة"}],
            companies)
        self.assertTrue(m)

    def test_machine_transliteration_kyrwy(self):
        with TemporaryDirectory() as tmp:
            companies = Matching()._companies(tmp)
            m = match_customers(
                [{"customer_id": "C5",
                  "name": "كروي للمقاولات والنقليات والخدمات",
                  "cr_number": None}],
                companies)
            names = {x["matched_name"].strip() for x in m}
            self.assertTrue(
                any("Kyrwy" in n or "KYRWY" in n.upper() for n in names),
                names)

    def test_cr_still_beats_arabic_name(self):
        with TemporaryDirectory() as tmp:
            companies = Matching()._companies(tmp)
            m = match_customers(
                [{"customer_id": "C1", "name": "شركة النجم",
                  "cr_number": "29309"}],
                companies)
            self.assertTrue(m)
            self.assertEqual({x["method"] for x in m}, {"cr"})



def _site_row(tid, name, cr=None, role="awarded", seq=0):
    from monaqasat.normalize import cr_root, normalize_name
    return {"tender_id": tid, "role": role, "seq": seq, "name": name,
            "name_normalised": normalize_name(name), "cr_number": cr,
            "cr_root": cr_root(cr)}


SITE = [
    _site_row("T1", "QATAR NAVIGATION Q.P.S.C", "11"),
    _site_row("T2", "QATAR ENGINEERING & CONSTRUCTION CO", "12"),
    _site_row("T3", "GULF WAREHOUSING COMPANY", "13"),
    _site_row("T4", "GULF DRILLING INTERNATIONAL", "14"),
    _site_row("T5", "MOHAMMED AL MANA TRADING", "15"),
    _site_row("T6", "Nisr Trading", "16"),
    _site_row("T7", "AMANA TRADING WLL", "17"),
    _site_row("T8", "Duha Trading", "18"),
    _site_row("T9", "QATAR PRESS", "30163"),
    _site_row("T10", "قطر للوقود (وقود)", "24872"),
    _site_row("T11", "Kyrwy Llmqawlat Walnqlyat Walkhdmlt", "99457"),
]


def _cust(cid, name, cr=None, name_ar=None):
    return {"customer_id": cid, "name": name, "cr_number": cr,
            "name_ar": name_ar}


class NoFalsePositives(unittest.TestCase):
    """Regressions from the 22 Sept 2026 review. Every customer here is a
    different company from every site row; none of them may match."""

    def assert_no_match(self, cust):
        m = match_customers([cust], SITE)
        self.assertEqual([(x["matched_name"], x["method"], x["score"])
                          for x in m], [])

    def test_shared_first_word_is_not_a_match(self):
        # used to match every QATAR company at score 1.0
        self.assert_no_match(_cust("B1", "Qatar Packaging Co"))
        self.assert_no_match(_cust("B2", "Gulf Packaging Industries"))
        self.assert_no_match(_cust("B3", "Mohammed Abdullah Contracting"))

    def test_same_consonants_are_not_an_exact_match(self):
        # used to be name_exact 1.0 through consonant skeleton keys
        self.assert_no_match(_cust("B4", "Nasser Trading"))
        self.assert_no_match(_cust("B5", "Al Amin Trading"))
        self.assert_no_match(_cust("B6", "Doha Trading"))

    def test_arabic_name_does_not_match_every_qatar_company(self):
        # قطر للتغليف = Qatar Packaging; used to match all QATAR rows
        self.assert_no_match(_cust("B7", "قطر للتغليف"))

    def test_false_positive_rate_on_unrelated_names(self):
        """60 customers not on the site against 6,000 site names. The first
        qdb-alt matcher produced 13,837 matches here; v0.3 produced 18."""
        import random
        rnd = random.Random(11)
        first = ["Qatar", "Gulf", "Doha", "Al Rayyan", "Al Mana", "Mohammed",
                 "Abdullah", "Star", "Modern", "United", "Arabian",
                 "Al Jazeera", "Lusail", "Pearl", "Falcon", "Al Khaleej",
                 "National", "Al Wakra", "Global", "Future", "Al Noor",
                 "Al Sadd", "Madina", "Nasser", "Amana", "Al Waha",
                 "Crystal", "Royal", "Elite", "Smart"]
        second = ["Blue", "Golden", "Green", "Silver", "Red", "Prime",
                  "First", "Grand", "Delta", "Alpha", "Omega", "Sky", "Sea",
                  "Sun", "Moon", "Palm", "Oasis", "Desert", "Horizon",
                  "Vision", "Summit", "Bridge", "Tower", "Harbor", "Crescent"]
        mid = ["Trading", "Contracting", "Engineering", "Printing",
               "Transport", "Services", "Technology", "Medical", "Catering",
               "Cleaning", "Security", "Steel", "Electrical", "Mechanical",
               "Furniture", "Consulting", "Logistics", "Marine", "Food",
               "Travel"]
        tail = ["WLL", "Co", "Company", "Est", "LLC", "Group", ""]
        from monaqasat.normalize import normalize_name
        seen, names = set(), []
        while len(names) < 6060:
            nm = (f"{rnd.choice(first)} {rnd.choice(second)} "
                  f"{rnd.choice(mid)} {rnd.choice(tail)}").strip()
            if normalize_name(nm) not in seen:
                seen.add(normalize_name(nm))
                names.append(nm)
        site = [_site_row(f"T{i}", nm, str(10000 + i))
                for i, nm in enumerate(names[:6000])]
        custs = [_cust(f"C{i}", nm) for i, nm in enumerate(names[6000:])]
        import time
        t0 = time.time()
        m = match_customers(custs, site)
        elapsed = time.time() - t0
        wrongly = {x["customer_id"] for x in m}
        self.assertLessEqual(len(m), 5, m[:5])
        self.assertLessEqual(len(wrongly), 5)
        self.assertLess(elapsed, 10.0, "matching must not be quadratic")


class TrueMatchesStillWork(unittest.TestCase):
    def test_spelling_variants(self):
        m = match_customers([_cust("C1", "Najm Al Farid Trading Co")],
                            [_site_row("T1", "NAJM ALFARID TRADING", "29309/1")])
        self.assertEqual([x["method"] for x in m], ["name_fuzzy"])
        self.assertGreaterEqual(m[0]["score"], 0.92)

    def test_arabic_script_on_the_site_matches_arabic_customer(self):
        # WOQOD appears in Arabic on the English site (tender 3217/2022)
        m = match_customers([_cust("C1", "قطر للوقود")], SITE)
        self.assertEqual([(x["matched_name"], x["method"]) for x in m],
                         [("قطر للوقود (وقود)", "name_exact")])

    def test_letter_by_letter_transliteration(self):
        m = match_customers(
            [_cust("C1", "", name_ar="كيروي للمقاولات والنقليات والخدمات")],
            SITE)
        self.assertEqual(
            [(x["matched_name"], x["method"]) for x in m],
            [("Kyrwy Llmqawlat Walnqlyat Walkhdmlt", "name_translit")])

    def test_cr_is_preferred_to_a_name_match_on_the_same_row(self):
        m = match_customers([_cust("C1", "QATAR PRESS", cr="30163")], SITE)
        self.assertEqual([(x["matched_name"], x["method"]) for x in m],
                         [("QATAR PRESS", "cr")])

    def test_cr_in_excel_formats(self):
        for cr in ("30,163", "CR-30163", "030163", "٣٠١٦٣", 30163.0):
            m = match_customers([_cust("C1", "x", cr=cr)], SITE)
            self.assertEqual([x["matched_name"] for x in m], ["QATAR PRESS"],
                             cr)

    def test_thousands_separator_is_not_a_short_cr(self):
        # "29,309" used to become CR 29 -- a different company
        site = [_site_row("T1", "SMALL CO", "29"),
                _site_row("T2", "NAJM ALFARID TRADING", "29309/1")]
        m = match_customers([_cust("C1", "x", cr="29,309")], site)
        self.assertEqual([x["matched_name"] for x in m],
                         ["NAJM ALFARID TRADING"])


class Classified(unittest.TestCase):
    def test_register_match_uses_the_same_rules(self):
        from monaqasat.match import match_classified
        reg = [{"profile_number": "CP-1", "name": "QATAR NAVIGATION",
                "cr_number": "11", "cr_root": "11"},
               {"profile_number": "CP-2", "name": "GULF DRILLING",
                "cr_number": "14", "cr_root": "14"}]
        hits = match_classified([_cust("C1", "Qatar Packaging"),
                                 _cust("C2", "x", cr="14")], reg)
        self.assertEqual([(h["customer_id"], h["profile_number"], h["method"])
                          for h in hits], [("C2", "CP-2", "cr")])


if __name__ == "__main__":
    unittest.main()
