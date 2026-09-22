"""Incremental crawling, against a simulated site.

The site is newest-first and changes between runs: tenders are published,
move from technical opening to award, and some pages time out. These tests
drive the real crawl code against it and count requests, because the whole
point of incremental crawling is the requests it does not make.
"""

import unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from monaqasat import cli
from monaqasat.fetch import Fetcher, PageTimeout
from monaqasat.store import Store

PER_PAGE = 20

CARD = ('<div class="row custom-cards"><div class="col-md-7 cards-col">'
        '<div class="col-header"><span class="card-label"><label>{num}</label>'
        '</span><span class="card-title"><a href="/TendersOnlineServices/'
        'TenderDetails/{id}">subject {id}</a></span></div><div class="col-footer">'
        '<div class="cards-row"><span class="card-label"><label>Award date'
        '</label></span><span class="card-title"><span><label>{date}'
        '</label></span></span></div></div></div><div class="col-md-3 cards-col">'
        '<div class="col-header"><span class="card-label"><label>Ministry</label>'
        '</span><span class="card-title"><span>Ministry {id}</span></span></div>'
        '</div><div class="col-md-2"><a href="/TendersOnlineServices/'
        'TenderCompaniesDetails/{id}">Report</a></div></div>')

AWARDED_TABLE = ('<h3>Awarded companies data</h3>'
                 '<table class="custom--table"><thead><tr><th>Company name</th>'
                 '<th>Commercial Registration Number</th><th>Approved Value</th>'
                 '<th>Financial Result</th><th>Approved Items</th></tr></thead>'
                 '<tbody><tr><td>CO {id}</td><td>{id} | 1</td>'
                 '<td>1,000.00 QAR</td><td></td><td></td></tr></tbody></table>')
OPENED_TABLE = ('<h3>Technically opened companies data</h3>'
                '<table class="custom--table"><thead><tr><th>Company name</th>'
                '<th>Commercial Registration Number</th></tr></thead><tbody>'
                '<tr><td>CO {id}</td><td>{id} | 1</td></tr>'
                '<tr><td>OTHER {id}</td><td>9{id} | 2</td></tr>'
                '</tbody></table>')
# The live site's companies page grows as a tender advances: names only at
# technical/financial opening, the winners (and prices) once awarded.
COMPANIES = ('<html><body><span id="lbl_num">{id}/2026</span>'
             '<span id="lbl_award">1,000.00</span>{tables}</body></html>')

DETAILS = ('<html><body><table><thead><tr><th>Activity code</th>'
           '<th>Activity name</th></tr></thead><tbody><tr><td>46900</td>'
           '<td>Wholesale</td></tr></tbody></table></body></html>')


HOME_PAGE = "<html><body><h2>Welcome to Monaqasat</h2></body></html>"


def award_date(tid) -> str:
    """One award a day, in id order: newest id, newest date -- the order
    the live Awarded list is in."""
    d = date(2020, 1, 1) + timedelta(days=int(tid) - 100000)
    return d.strftime("%d/%m/%Y")


class FakeSite:
    """A listing per section, served page by page, newest first unless a
    test puts a tender somewhere else."""

    def __init__(self):
        self.sections: dict[str, list[str]] = {}   # path -> ids, top first
        self.requests: list[str] = []
        self.hang: set[str] = set()                # paths that time out
        self.home: set[str] = set()                # ids answered with home page
        self.no_winner_yet: set[str] = set()       # awarded, table not up yet
        self.dates: dict[str, str] = {}            # id -> award date override
        self._next = 100000

    def publish(self, path: str, n: int) -> list[str]:
        ids = [str(self._next + i) for i in range(n)]
        self._next += n
        self.sections.setdefault(path, [])[:0] = list(reversed(ids))
        return ids

    def move(self, tid: str, src: str, dst: str) -> None:
        self.sections[src].remove(tid)
        self.sections.setdefault(dst, []).insert(0, tid)

    def awarded(self, tid: str) -> bool:
        return any(tid in ids for path, ids in self.sections.items()
                   if path.startswith("Awarded"))

    def get(self, rel: str):
        self.requests.append(rel)
        if rel in self.hang:
            raise PageTimeout(f"{rel}: no answer")
        parts = rel.strip("/").split("/")
        if parts[1] in ("TenderDetails",):
            return (HOME_PAGE if parts[2] in self.home else DETAILS), 200
        if parts[1] == "TenderCompaniesDetails":
            tid = parts[2]
            if tid in self.home:
                return HOME_PAGE, 200
            tables = OPENED_TABLE.format(id=tid)
            if self.awarded(tid) and tid not in self.no_winner_yet:
                tables = AWARDED_TABLE.format(id=tid) + tables
            return COMPANIES.format(id=tid, tables=tables), 200
        path, page = parts[1], int(parts[2])
        ids = self.sections.get(path, [])
        last = max(1, -(-len(ids) // PER_PAGE))
        page = min(page, last)          # the live site: past the end -> last
        chunk = ids[(page - 1) * PER_PAGE: page * PER_PAGE]
        cards = "".join(CARD.format(id=i, num=f"{i}/2026",
                                    date=self.dates.get(i, award_date(i)))
                        for i in chunk)
        pager = "".join(f'<a href="/TendersOnlineServices/{path}/{p}">{p}</a>'
                        for p in (1, 2, last))
        return f"<html><body>{cards}<nav>{pager}</nav></body></html>", 200

    def count(self, fragment: str) -> int:
        return sum(fragment in r for r in self.requests)

    def reset(self):
        self.requests.clear()


def _args(**kw):
    base = dict(db=None, kind="awarded", pages=None, full=False, stop_after=2,
                limit_details=None, delay=0, timeout=5, listings_only=False,
                no_details=False, quick=False, lookback_days=14,
                roll_pages=100, sweep_days=7)
    base.update(kw)
    return Namespace(**base)


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.db")
        self.site = FakeSite()
        patches = [
            mock.patch.object(Fetcher, "check_robots", lambda self: ""),
            mock.patch.object(Fetcher, "get",
                              lambda self, rel: self_site.get(rel)),
        ]
        self_site = self.site
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self._quiet = mock.patch("builtins.print")
        self._quiet.start()
        self.addCleanup(self._quiet.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def crawl(self, **kw):
        self.site.reset()
        return cli.cmd_crawl(_args(db=self.db, **kw))

    def store(self):
        return Store(self.db)


class FirstLoad(Harness):
    def test_first_run_takes_everything(self):
        self.site.publish("AwardedTenders", 100)          # 5 pages
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 100)
        self.assertEqual(s.stats()["award rows"], 100)
        self.assertTrue(s.section("awarded")["backfill_complete"])
        s.close()

    def test_first_run_request_count(self):
        self.site.publish("AwardedTenders", 100)
        self.crawl()
        # 5 listing pages; 100 tenders x 2 detail pages
        self.assertEqual(self.site.count("AwardedTenders/"), 5)
        self.assertEqual(self.site.count("TenderDetails/"), 100)
        self.assertEqual(self.site.count("TenderCompaniesDetails/"), 100)


class Rerun(Harness):
    def test_nothing_new_means_almost_nothing_fetched(self):
        self.site.publish("AwardedTenders", 100)
        self.crawl()
        self.crawl()
        # page 1 plus the stop-after pages, and no detail pages at all
        self.assertLessEqual(self.site.count("AwardedTenders/"), 3)
        self.assertEqual(self.site.count("TenderDetails/"), 0)
        self.assertEqual(self.site.count("TenderCompaniesDetails/"), 0)

    def test_only_new_tenders_get_detail_pages(self):
        self.site.publish("AwardedTenders", 100)
        self.crawl()
        new = self.site.publish("AwardedTenders", 25)      # pushes pages down
        self.crawl()
        fetched = {r.rsplit("/", 1)[1] for r in self.site.requests
                   if "TenderCompaniesDetails/" in r}
        self.assertEqual(fetched, set(new))
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 125)
        s.close()

    def test_new_tenders_are_found_even_though_pages_shifted(self):
        """The bug page-number tracking would have: page 1 'already done'."""
        self.site.publish("AwardedTenders", 100)
        self.crawl()
        self.site.publish("AwardedTenders", 7)
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 107)
        s.close()

    def test_rerun_reads_few_listing_pages_not_all(self):
        self.site.publish("AwardedTenders", 400)           # 20 pages
        self.crawl()
        self.site.publish("AwardedTenders", 30)
        self.crawl()
        self.assertLessEqual(self.site.count("AwardedTenders/"), 5)


class Interrupted(Harness):
    def test_an_interrupted_first_load_is_finished_later(self):
        self.site.publish("AwardedTenders", 400)           # 20 pages
        self.crawl(pages="1-5")                            # stop early
        s = self.store()
        self.assertFalse(s.section("awarded")["backfill_complete"])
        self.assertEqual(s.stats()["tenders"], 100)
        s.close()

        self.site.publish("AwardedTenders", 10)            # and time passes
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 410)
        self.assertTrue(s.section("awarded")["backfill_complete"])
        s.close()

    def test_resume_does_not_restart_from_page_one(self):
        self.site.publish("AwardedTenders", 400)
        self.crawl(pages="1-10")
        self.crawl()
        pages = sorted(int(r.rsplit("/", 1)[1]) for r in self.site.requests
                       if "AwardedTenders/" in r)
        # top couple of pages to catch up, then from ~page 7 onward
        self.assertNotIn(5, pages)
        self.assertIn(20, pages)

    def test_details_interrupted_are_finished_without_rereading(self):
        self.site.publish("AwardedTenders", 60)
        self.crawl(limit_details=20)
        s = self.store()
        self.assertLess(s.stats()["award rows"], 60)
        s.close()
        self.crawl()
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["award rows"], 60)
        s.close()


class ExplicitRange(Harness):
    def test_rerunning_a_range_skips_what_is_stored(self):
        """The case you asked about: run 100 pages when 90 are there."""
        self.site.publish("AwardedTenders", 90 * PER_PAGE)
        self.crawl(pages="1-90")
        self.site.publish("AwardedTenders", 10 * PER_PAGE)
        self.crawl(pages="1-100")
        details = self.site.count("TenderCompaniesDetails/")
        # only the 200 new tenders, not 2,000
        self.assertEqual(details, 200)


class StateChanges(Harness):
    def test_award_refetches_companies_but_not_description(self):
        ids = self.site.publish("TechnicallyOpenedTenders", 20)
        self.crawl(kind="technical")
        s = self.store()
        self.assertEqual(s.current_state(ids[0]), "technical")
        s.close()

        self.site.move(ids[0], "TechnicallyOpenedTenders", "AwardedTenders")
        self.crawl(kind="awarded")
        self.assertEqual(self.site.count(f"TenderCompaniesDetails/{ids[0]}"), 1)
        self.assertEqual(self.site.count(f"TenderDetails/{ids[0]}"), 0)
        s = self.store()
        self.assertEqual(s.current_state(ids[0]), "awarded")
        s.close()

    def test_crawling_an_earlier_section_later_never_demotes(self):
        ids = self.site.publish("AwardedTenders", 5)
        self.crawl(kind="awarded")
        self.site.sections["TechnicallyOpenedTenders"] = list(ids)
        self.crawl(kind="technical")
        s = self.store()
        self.assertEqual(s.current_state(ids[0]), "awarded")
        s.close()

    def test_lifecycle_is_recorded(self):
        ids = self.site.publish("TechnicallyOpenedTenders", 3)
        self.crawl(kind="technical")
        self.site.move(ids[0], "TechnicallyOpenedTenders", "AwardedTenders")
        self.crawl(kind="awarded")
        s = self.store()
        kinds = {r[0] for r in s.conn.execute(
            "SELECT kind FROM sighting WHERE tender_id=?", (ids[0],))}
        self.assertEqual(kinds, {"technical", "awarded"})
        s.close()


class SlowPages(Harness):
    def test_a_timeout_is_skipped_not_fatal(self):
        self.site.publish("AwardedTenders", 100)
        self.site.hang.add("/TendersOnlineServices/AwardedTenders/3")
        rc = self.crawl()
        self.assertEqual(rc, 0)
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 80)       # all but page 3
        self.assertEqual(len(s.failures("awarded")), 1)
        s.close()

    def test_a_skipped_page_is_recovered_next_run(self):
        self.site.publish("AwardedTenders", 100)
        self.site.hang.add("/TendersOnlineServices/AwardedTenders/3")
        self.crawl()
        self.site.hang.clear()
        self.crawl(full=True)
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 100)
        self.assertEqual(len(s.failures("awarded")), 0)
        s.close()

    def test_a_skipped_listing_page_is_recovered_by_a_normal_update(self):
        """Without --full. A gap must not be sealed over as 'complete'."""
        self.site.publish("AwardedTenders", 100)
        self.site.hang.add("/TendersOnlineServices/AwardedTenders/3")
        self.crawl()
        s = self.store()
        self.assertFalse(s.section("awarded")["backfill_complete"],
                         "a load with a hole in it is not complete")
        s.close()
        self.site.hang.clear()
        self.site.publish("AwardedTenders", 25)      # and pages shift
        self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 125)
        self.assertEqual(len(s.failures("awarded")), 0)
        self.assertTrue(s.section("awarded")["backfill_complete"])
        s.close()

    def test_recovery_rereads_the_gap_not_the_whole_section(self):
        self.site.publish("AwardedTenders", 1000)    # 50 pages
        self.site.hang.add("/TendersOnlineServices/AwardedTenders/10")
        self.crawl()
        self.site.hang.clear()
        self.site.publish("AwardedTenders", 45)      # gap drifts ~2 pages
        self.crawl()
        # the gap was actually filled ...
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 1045)
        self.assertEqual(len(s.failures("awarded")), 0)
        s.close()
        # ... by reading its neighbourhood, not all 53 pages
        self.assertLess(self.site.count("AwardedTenders/"), 15)

    def test_a_failed_detail_page_is_retried_next_run(self):
        ids = self.site.publish("AwardedTenders", 10)
        self.site.hang.add(f"/TendersOnlineServices/TenderCompaniesDetails/{ids[0]}")
        self.crawl()
        self.site.hang.clear()
        self.crawl()
        self.assertEqual(self.site.count(f"TenderCompaniesDetails/{ids[0]}"), 1)

    def test_a_run_of_failures_stops_the_section(self):
        self.site.publish("AwardedTenders", 400)
        for p in range(2, 12):
            self.site.hang.add(f"/TendersOnlineServices/AwardedTenders/{p}")
        self.crawl(full=True)
        pages_tried = self.site.count("AwardedTenders/")
        self.assertLess(pages_tried, 20, "must not hammer a failing site")


class RunLog(Harness):
    def test_each_run_is_recorded_with_what_it_did(self):
        self.site.publish("AwardedTenders", 40)
        self.crawl()
        self.site.publish("AwardedTenders", 5)
        self.crawl()
        s = self.store()
        runs = s.runs()
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["new_tenders"], 5)
        self.assertEqual(runs[1]["new_tenders"], 40)
        s.close()


class ManyRuns(Harness):
    def test_weekly_runs_stay_cheap_forever(self):
        """Ten updates, each after a week of new awards. None should drift
        into re-reading old pages."""
        self.site.publish("AwardedTenders", 1000)        # 50 pages
        self.crawl()
        counts = []
        for _ in range(10):
            self.site.publish("AwardedTenders", 20)
            self.crawl()
            counts.append(self.site.count("AwardedTenders/"))
        self.assertTrue(all(c <= 4 for c in counts), counts)
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 1200)
        self.assertTrue(s.section("awarded")["backfill_complete"])
        s.close()


# --------------------------------------------------------------------------
# the classified register -- oldest first, new companies on the last page
# --------------------------------------------------------------------------

REG_ROW = ('<tr class="text-center"><td>{n}</td><td>{pid}</td>'
           '<td>Local company</td><td>COMPANY {pid}</td><td>{cr} | 1</td>'
           '<td>Small</td><td><label>Good</label></td><td class="actions">'
           '<a href="javascript:openCompanyDataModel({n});">x</a></td></tr>')
REG_MODAL = ('<div id="CompanyData-{n}"><div class="alert alert-success">'
             'Supplier Classification - Certificate end date: 01/01/2027</div>'
             '<table><thead><tr><th>S</th><th>Activity code</th>'
             '<th>Activity name</th><th>Classification grade</th></tr></thead>'
             '<tbody><tr><td>1</td><td>46900</td><td>Trade</td><td>Fifth</td>'
             '</tr></tbody></table></div>')


class FakeRegister:
    def __init__(self, n):
        self.pids = [f"CP-{i:06d}" for i in range(n)]      # oldest first
        self.requests = []
        self.hang = set()

    def add(self, n):
        start = len(self.pids)
        self.pids += [f"CP-{i:06d}" for i in range(start, start + n)]

    def get(self, rel):
        self.requests.append(rel)
        if rel in self.hang:
            raise PageTimeout(rel)
        page = int(rel.rsplit("/", 1)[1])
        last = max(1, -(-len(self.pids) // PER_PAGE))
        chunk = self.pids[(page - 1) * PER_PAGE: page * PER_PAGE]
        rows = "".join(REG_ROW.format(n=i, pid=pid, cr=90000 + int(pid[3:]))
                       for i, pid in enumerate(chunk, (page - 1) * PER_PAGE))
        modals = "".join(REG_MODAL.format(n=i) for i, _ in
                         enumerate(chunk, (page - 1) * PER_PAGE))
        pager = "".join(f'<a href="/ClassifiedCompaniesProfilesList/{p}">{p}</a>'
                        for p in (1, 2, last))
        head = ('<table class="custom--table"><thead><tr><th>S</th>'
                '<th>Profile number</th><th>Company type</th><th>Company name'
                '</th><th>Commercial Registration Number</th><th>Company size'
                '</th><th>Government entities evaluation</th><th>Activities '
                'details</th></tr></thead><tbody>')
        return (f"<html><body>{head}{rows}</tbody></table>{modals}"
                f"<nav>{pager}</nav></body></html>"), 200

    def count(self):
        return len(self.requests)


class Register(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "r.db")
        self.reg = FakeRegister(200)                     # 10 pages
        reg = self.reg
        for p in (mock.patch.object(Fetcher, "check_robots", lambda s: ""),
                  mock.patch.object(Fetcher, "get", lambda s, rel: reg.get(rel)),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, **kw):
        self.reg.requests.clear()
        a = dict(db=self.db, pages=None, full=False, delay=0, timeout=5)
        a.update(kw)
        return cli.cmd_companies(Namespace(**a))

    def companies(self):
        s = Store(self.db)
        n = s.stats()["classified companies"]
        s.close()
        return n

    def test_first_load_takes_all(self):
        self.run_()
        self.assertEqual(self.companies(), 200)

    def test_update_reads_only_the_tail(self):
        self.run_()
        self.reg.add(30)                                 # lands on pages 10-12
        self.run_()
        self.assertEqual(self.companies(), 230)
        # page 1 to learn the size, then the tail -- not all 12 pages
        self.assertLessEqual(self.reg.count(), 5)

    def test_nothing_new_is_nearly_free(self):
        self.run_()
        self.run_()
        self.assertLessEqual(self.reg.count(), 3)

    def test_the_timeout_you_hit_is_skipped_and_retried(self):
        """Page 2 hung for you. It must not stop the other 243."""
        self.reg.hang.add("/ClassifiedCompaniesProfilesList/2")
        self.assertEqual(self.run_(), 0)
        self.assertEqual(self.companies(), 180)
        s = Store(self.db)
        self.assertEqual([r["ref"] for r in s.failures("classified")], ["2"])
        self.assertFalse(s.section("classified")["backfill_complete"])
        s.close()

        self.reg.hang.clear()
        self.run_()
        self.assertEqual(self.companies(), 200)
        self.assertIn("/ClassifiedCompaniesProfilesList/2", self.reg.requests)
        s = Store(self.db)
        self.assertEqual(s.failures("classified"), [])
        self.assertTrue(s.section("classified")["backfill_complete"])
        s.close()

    def test_full_refresh_rereads_everything(self):
        self.run_()
        self.run_(full=True)
        self.assertEqual(self.reg.count(), 11)           # page 1 + all 10

    def test_interrupted_register_load_resumes(self):
        self.run_(pages="1-4")
        self.assertEqual(self.companies(), 80)
        self.run_()
        self.assertEqual(self.companies(), 200)
        self.assertNotIn("/ClassifiedCompaniesProfilesList/2",
                         self.reg.requests[1:])


class TimeBudget(Harness):
    def test_expired_deadline_stops_and_next_run_continues(self):
        self.site.publish("AwardedTenders", 200)
        args = _args(db=self.db, kind="awarded")
        args.deadline = 0
        with mock.patch("builtins.print"):
            cli.cmd_crawl(args)
        s = self.store()
        n1 = s.stats()["tenders"]
        s.close()
        self.assertGreaterEqual(n1, 1)
        self.assertLess(n1, 200)

        with mock.patch("builtins.print"):
            self.crawl()
        s = self.store()
        self.assertEqual(s.stats()["tenders"], 200)
        s.close()


if __name__ == "__main__":
    unittest.main()
