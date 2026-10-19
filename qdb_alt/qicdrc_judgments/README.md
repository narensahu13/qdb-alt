# QICDRC judgments

Another alternate-data source beside [`monaqasat`](../monaqasat) and
[`icv`](../icv): the published judgments of the Qatar International Court and
Dispute Resolution Centre ([qicdrc.gov.qa](https://www.qicdrc.gov.qa)), the
court of the Qatar Financial Centre -- about 570 judgments since 2009, from
the First Instance Circuit, the Appellate Division, the Regulatory Tribunal
and enforcement, each with its parties, case number, neutral citation, date
and the court's keywords.

For a borrower it answers "has this company been in court, on which side,
how recently". A company that keeps turning up as a defendant over unpaid
fees or salaries is telling a lender something.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` is there for the same reason as in `icv`: behind QDB's TLS proxy
`requests` needs the Windows certificate store.

## Loading it

```powershell
python -m qicdrc_judgments load
python -m qicdrc_judgments stats
```

The site's robots.txt asks for 10 seconds between requests and disallows the
paging and sorting query strings of its lists (`/*?page=*&`, `/*&page=`,
`/*?sort=`, `/*?order=` ...), so the paged judgments list is off limits. The
judgments are read the way a search engine reads them instead: the sitemap,
then each English judgment page (the Arabic copies are skipped), one request
every 10 seconds. `load` reads robots.txt first and checks every URL against
it before requesting it (RFC 9309: longest match wins, `*` and `$`; see
`robots.py`); a page it disallows is not fetched and the run note says so.

The first load is about 570 pages, some 95 minutes. It does not have to be
done in one go: it commits every 25 pages, and

```powershell
python -m qicdrc_judgments load --max-pages 100     # or --max-minutes 30
```

stops cleanly, saying how many pages are left; the next `load` carries on.
After that, `load` fetches only judgments that are new or whose last-modified
date in the sitemap changed -- usually a handful. A judgment that leaves the
sitemap is marked as no longer listed rather than deleted.

A page that cannot be read (an HTTP error, or a page without a case number
or citation, which would mean the layout changed) is recorded in
`failed_page` and fetched again on the next load. The run note says how many
failed; if more than one page in ten failed, the run is logged as an error.
Ten failures in a row stop the run: the site is down or refusing us.

## What is kept

Per judgment: case number, neutral citation, court, date, the court's
keywords, an outcome where the court's own keywords state one, the judgment
URL, and the parties that are companies or other organisations (`party`
table: judgment, side, name). People who are parties -- employees suing for
their salary, guarantors -- are kept only as a count
(`n_individual_parties`); `case_title` shows them that way
("1 individual v Example Studios LLC").

The pages themselves are not kept, unlike in the other sources here, because
they name those people. So a change to the parser needs
`load --refetch` (another 95 minutes) rather than an offline re-read.

On the October 2026 site: 568 judgments (341 cases, 2009-2026), 818
company-party entries (232 distinct names), 362 judgments with at least one
individual party, 52 with an outcome keyword. The first load took 95 minutes.

## Matching the customer book

```powershell
python -m qicdrc_judgments match customers.csv --out qicdrc_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`, the trading name to search for when
the registered name differs). One row per customer comes out:

| column | meaning |
|---|---|
| `qicdrc_cases` | judgments in which the customer is a party |
| `qicdrc_distinct_cases` | the same, counted by case number (a case can have several judgments) |
| `qicdrc_as_claimant`, `qicdrc_as_defendant` | judgments with the customer on the first / second named side |
| `qicdrc_last_judgment_year` | year of the latest of them |
| `qicdrc_outcomes` | outcomes stated in the court's keywords (e.g. `appeal dismissed`, `default judgment`) |
| `qicdrc_matched_parties` | the party names that matched, for a person to check |
| `qicdrc_citations`, `qicdrc_urls` | the judgments, oldest first |

A customer that is a party to nothing has 0 in the counts and every other
column empty.

The court publishes no CR numbers, so the join is by name and it is strict:
`same_company` in `namematch.py` (the same file as in the other name-searched
sources), which wants every distinctive word of the customer's name in the
party's and the brand word exactly. Each party name is tried whole and cut at
commas and at the word "and", because a heading may join two companies with
"and" while a company's own name may contain it (*Al Wajba Dairy and Juices
Factory*). If the database is not fully loaded, `match` says how many pages
are still missing.

## The run log

Every command is recorded in the `run` table: start, finish, duration, rows
written, a note ("568 judgments in the sitemap, 120 pages fetched, 448 left
for the next load (stopped at --max-pages 120)") and, if it failed, the error.

```powershell
python -m qicdrc_judgments runs
```

## Tests

```powershell
python -m unittest discover -s tests
```

They run offline against a stand-in site (`tests/fake_site.py`) and a made-up
judgment page in the real markup.

## Known limits

- QICDRC is the QFC's court. Most Qatari companies' disputes go to the
  ordinary Qatari courts, which publish nothing comparable, so
  `qicdrc_cases = 0` means "no QFC court judgment", not "no litigation".
- Parties come from the heading and the page title. Some headings end in
  "and Others" and some titles give only the case number, so a co-defendant
  named only inside the PDF is missed.
- A few headings name several cases at once ("A v B and C v D"); there the
  names between the first and the last 'v' cannot be put on a side and are
  recorded as `party` (counted in `qicdrc_cases`, not in either side column).
- "Claimant" and "defendant" are the first and second named sides. On an
  appeal that is the appellant and the respondent, who may be the other way
  round from the first-instance case; enforcement and tribunal matters follow
  the same order.
- Whether a party is a company or a person is read from its name (LLC, WLL,
  Bank, Authority ...). An organisation with no such word in its name is
  counted as a person, and so cannot be matched; anonymised parties ("A v B")
  are counted as people too.
- The outcome is filled only when one of the court's keywords states it
  (appeal dismissed / allowed, permission refused, default judgment, claim
  withdrawn) -- a minority of judgments. The page's summary is
  machine-written (the site says so) and is not used.
- Judgment URLs can contain the names of individual parties; they are the
  court's own public addresses and are kept so a match can be checked.
