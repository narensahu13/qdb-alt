# ICV (Tawteen) — design

Second alternate-data source, beside `qdb_alt/monaqasat`. Where Monaqasat says
*what a company won from the state*, ICV says *how much of what it sells is made
in Qatar* — a score QatarEnergy and its operating companies use when awarding
work. For a borrower in that supply chain the score is close to a licence to
bid, and its movement is a leading indicator of the revenue we are lending
against.

Written on 24 Sep 2026 from the live portal, and revised the same day once the
crawler was built: several things that looked obvious from the page turned out
to be wrong when measured, and each of those is now marked **measured** below.
Nothing here is an estimate unless it says so.

## 1. What the source actually is

Public, no login: <https://icv.tawteen.com.qa/en-US/supplier-database-guest/>

It is a Microsoft Power Pages (Dataverse) portal, which matters more than it
sounds: the grid on that page is not HTML to be scraped but a JSON service.

```
POST /_services/entity-grid-data.json/7b138792-1090-45b6-9241-8f8d96d8c372
  headers: __RequestVerificationToken (from the page), X-Requested-With
  body:    { base64SecureConfiguration, sortExpression, search, page,
             pageSize, pagingCookie, filter, metaFilter, nlSearchFilter,
             timezoneOffset, customParameters }
```

`base64SecureConfiguration` is the list's signed configuration. It is not in
the page as-is: each `div.entity-grid` carries a `data-view-layouts`
attribute, base64 of a JSON array, and the entry whose `Id` matches
`data-selected-view` holds it under `Base64SecureConfiguration`. Without it the
service answers 403.

The response carries `Records[]`, `MoreRecords`, `ItemCount`, `PageCount` and
`NextPagePagingCookie`.

### Fields, by their Dataverse logical names

| Field | Meaning |
|---|---|
| `accountid` | GUID, the portal's own key — survives name and CR changes |
| `icv_commercialregistrationnumber` | CR number (`185319`, or `QFC/00326`) |
| `name` | company name |
| `icv_totalscore` | Total ICV Score %, a formula: `min(100, icv + manufacturer + bonus + transitional)` |
| `icv_mainactivity` | industry (Engineering and Construction, Manufacturing, IT, …) |
| `icv_latestcertificate` | **measured:** a *lookup to the certificate record*, displayed as the certificate **number** (`10011086`) — not a date, though it is the default sort key, descending, and the numbers increment so that is newest-first |
| `icv_icvscoreexpirydate` | grace-period expiry date |
| `statuscode` (on the linked certificate) | Draft / Pending Supplier Confirmation / Pending Certifier Confirmation / **Valid** / Under Re-assessment / … |
| `icv_ismanufacturer` | **measured:** present on some records and *absent* on others. Attributes with no value are omitted, not nulled |
| `icv_companynamecrsearch` | name + CR + goods and services concatenated; this is what the portal's search box matches, so a CR number finds exactly one company |

### Four measured facts that shape everything below

**a. The register holds 6,892 companies, not the 5,000 the page reports.**
The UI stops at page 250 and the service returns `ItemCount: 5000` at every
page size. Both are wrong: paging past 250 keeps returning real, distinct
records. Swept to exhaustion at `pageSize 1000`, the register is
**6,892 distinct `accountid`s** — six full pages plus 892, with no duplicates
between pages. Asking for page 8 serves page 7 again rather than nothing, so
a sweep has to stop on `MoreRecords` *and* on a page that brings nothing new.
`ItemCount` must never be used.

**b. `NextPagePagingCookie` comes back empty on the guest list.** The first
draft of this design said to page with the cookie and never by page number.
That is not available here, and page numbers turned out to be reliable at
`pageSize 1000` — the earlier 7,072-vs-7,122 discrepancy was arithmetic on the
clamped last page, not duplicate records.

**c. The whole listing costs seven requests.** 6,892 records at `pageSize
1000`. This is the biggest difference from Monaqasat, where the listing alone
is 1,200+ HTML pages and had to be budgeted, sliced and rolled. Here there is
no need for watermarks, rolling passes or a listing/detail time split: sweep
the entire listing on every run.

**d. The score history is already published, per company, back to inception.**
`/supplier-score-history/?id=<GUID>&status=<code>` carries a subgrid of
`icv_scorecardhistory` rows — score, posted date, reason, change — fetched from
`/_services/entity-subgrid-data.json/...`. A real example:

| Score | Posted (UTC) | Reason | Change |
|---|---|---|---|
| 10.84 | 2024-03-28 | New Certificate Issued | +10.84 |
| 10.85 | 2024-08-18 | Sub Supplier Score Updated | +0.01 |
| 17.85 | 2024-11-23 | Transitional Percentage Added | +7.00 |
| 18.91 | 2025-06-26 | Certificate Renewed | +1.06 |
| 19.13 | 2025-07-31 | Sub Supplier Score Updated | +0.22 |
| 18.97 | 2026-09-23 | Certificate Renewed | −0.16 |

This is why the design is a mirror and not a lookup: we do not have to spend
two years accumulating a panel. **One backfill reconstructs the entire history
of every company today.**

Each row carries its own `icv_scorecardhistoryid`, which is the primary key the
store uses — better than a position in the series, because new rows arrive at
the top and positions would shift.

**And the trap:** the signed configuration carries the *identity of the
company*. Reusing one page's configuration with a different `entityId` returns
the first company's history, silently. Measured, by asking for company B with
company A's configuration and getting A's six rows back. So a company's history
can only be fetched after fetching that company's own page — two requests per
company, not one.

## 2. The decision, and why

**Mirror the whole portal. Do not look the score up when an application
arrives.** An application-time lookup returns *today's* score, which quietly
ruins the three things the score is for:

1. **Point-in-time correctness.** Scoring a 2024 default with a 2026 ICV score
   is look-ahead bias. IFRS 9 and PD work needs the value as at the observation
   date, and the published history gives exactly that.
2. **A control population.** The ~5,900 companies that are not our customers
   are the sector medians and the peer distribution. "24%" means nothing;
   "24% against a Manufacturing median of 38%" is a feature.
3. **No external site in the credit path.** A loan application must not stall
   because Tawteen is down or changed its markup.

A live lookup stays, as a *fallback* for an applicant we have never seen, not
as a second system: `refresh --cr <n>` runs the same parse and writes the same
rows.

## 3. The store

SQLite, one file (`icv.db`), same conventions as `monaqasat` — GUID keys,
`first_seen`/`last_seen`, a `run` table, and a `pack` that leaves customer
identities behind. See `icv/store.py` for the schema as built; the shape is:

- **`company`** — one row per supplier, keyed by `accountid`. `cr_root` is the
  joinable form of the CR number. `misses` and `gone_at` close a company that
  has left two consecutive complete sweeps; its rows are kept.
- **`score_observation`** — the published series, keyed by the portal's own
  `icv_scorecardhistoryid`. Append-only: a row is written once and never
  edited, so `first_seen` records when we learned of it and the row itself
  never moves.
- **`listing_state`** — status, certificate number and expiry, as periods with
  `from_date` and `to_date`. Type-2, because these exist *only* in the listing.
  Two sweeps on one day replace rather than stack: a period that starts and
  ends on the same date is one no point-in-time query could ever return.
- **`company_service` / `company_good`** — what the supplier sells. Two more
  requests per company, so off unless `--goods` is given.
- **`raw_page`**, **`run`**, **`crawl_state`**, **`match`**.

### Reading it point-in-time

```sql
-- score as at :asof
SELECT score, posted_date, reason FROM score_observation
 WHERE company_id = :id AND posted_date <= :asof
 ORDER BY posted_date DESC LIMIT 1;

-- standing as at :asof
SELECT status, expiry_date FROM listing_state
 WHERE company_id = :id AND from_date <= :asof
   AND (to_date IS NULL OR to_date >= :asof);
```

Before a company's first certificate the first query returns nothing — which is
the right answer, and is not the same as a score of zero.

**Score history is retrospective and free. Status history is prospective
only** — `listing_state` starts the day the first sweep runs. That is the
argument for starting the monthly sweep now, before any model is written.

### Dates

`icv_posteddate` arrives three ways: `/Date(1790204472000)/` in `Value`, UTC
ISO in `DisplayValue`, and Doha local time in `FormattedValue`. The last two
can name different calendar days — the newest row above renders as 24/09/2026
in Doha and is 2026-09-23T23:01:12Z. The store keeps UTC throughout; mixing the
two puts an observation in the wrong month at a month end.

## 4. How a run works

**Backfill, once.** Seven requests for the list, then two per company: about
**13,800 requests, roughly six hours** at the 1.5 s delay. (The first draft of
this design said three hours, on the assumption of one request per company.
See the trap in §1d.) It is resumable — `history_read` is null until a
company's history has been read, so an interrupted run simply continues.

**Steady state, monthly.** The full list sweep again, because it is a minute.
Then a detail page only where the listing moved — `total_score`, `status`,
`certificate_no` or `expiry_date` changed — or the company is new. Expect a few
hundred a month, so a run is minutes.

A company missing from two consecutive *complete* sweeps gets `gone_at` set. An
incomplete sweep marks nothing: a half-read list would otherwise retire
thousands of live companies.

**Live fallback.** `refresh --cr 29309` puts the CR in the endpoint's `search`,
takes the one record, fetches its page and history, and writes the same rows a
sweep would.

### CLI

```
python -m icv crawl [--max-hours H] [--limit N] [--listings-only]
                    [--changed-only] [--goods]
python -m icv refresh --cr <n> | --name "<name>"
python -m icv status | stats
python -m icv match --customers customer-data\customers.csv --out matches.csv
python -m icv export --table companies|scores|listing|matches --out f.csv
python -m icv pack        # excludes raw_page and match
python -m icv doctor      # five named checks against the live portal
```

## 5. Matching to the customer book

Same ladder as Monaqasat — `cr` → `name_exact` → `name_fuzzy` (token_sort
≥ 0.92, same script) → `name_translit` (consonant skeletons ≥ 0.90,
cross-script) — with `normalize.py` and `match.py` copied into this package
rather than shared, until a second consumer proves what they have in common.

One extension: `cr_root` learns the QFC form. `QFC/00326` normalises to
`QFC326`, not `326`. Qatar Financial Centre registrations run in their own
sequence, so stripping the prefix would join a borrower to an unrelated
mainland company. There is a test for exactly that.

As in Monaqasat, one customer may hold several rows in the book — a changed
registration, a subsidiary, a QFC entity — and every row is matched with the
results filed under the one customer id.

## 6. What to be careful about when modelling

- **Separate policy from performance.** `Transitional Percentage Added +7.00`
  is a rule change, not a company improving its local content. Feed raw deltas
  into a PD model and it will learn a regulation. `reason` is on every
  observation and `stats` groups by it, so this stays visible.
- **The headline score is a capped sum**, `min(100, icv + manufacturer + bonus
  + transitional)`. A company at 100.00 may be well past 100 underneath, so the
  top of the range is censored — treat it as such.
- **Lapse and grace are signals.** A certificate allowed to expire, or a move
  to Grace Period or Under Re-assessment, speaks to a borrower's ability to keep
  winning sector work. It lives only in `listing_state`.
- **Sector medians move.** Compare a borrower to its `industry` cohort at the
  same date, not to a fixed threshold — `Store.industry_scores_as_at`.

## 7. Still open

- **The date picker on the listing.** Setting it and re-querying did not
  retro-date any score in testing, so its purpose is still unknown. If it does
  serve the list as at a past date, `listing_state` becomes retrospective too,
  which would be worth a great deal — it is the one part of this data we
  otherwise cannot recover for the past.
- **How much of the raw response to keep.** Today the crawler stores nothing by
  default; `Store.save_raw` exists and `fetch.slim` strips the Dataverse
  attribute metadata that makes a 1,000-record page run to megabytes. Monaqasat
  keeps pages because the evidence mattered for tender amounts. Decide after
  the first backfill, when the real sizes are known.
- **Whether `--goods` is worth its two extra requests per company** across the
  whole register, or only for matched customers and their cohort.
