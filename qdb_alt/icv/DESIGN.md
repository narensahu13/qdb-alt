# ICV (Tawteen) — design

Second alternate-data source, beside `qdb_alt/monaqasat`. Where Monaqasat says
*what a company won from the state*, ICV says *how much of what it sells is made
in Qatar* — a score QatarEnergy and its operating companies use when awarding
work. For a borrower in that supply chain the score is close to a licence to
bid, and its movement is a leading indicator of the revenue we are lending
against.

Nothing is built yet. This is the design, written after reading the live
portal on 24 Sep 2026. Every number below was measured, not assumed.

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

`base64SecureConfiguration` is the list's signed configuration, lifted from the
page markup; it is what makes the endpoint callable at all. The response
carries `Records[]`, `MoreRecords`, `ItemCount`, `PageCount` and
`NextPagePagingCookie`.

### Fields, by their Dataverse logical names

| Field | Meaning |
|---|---|
| `accountid` | GUID, the portal's own key — survives name and CR changes |
| `icv_commercialregistrationnumber` | CR number (`185319`, or `QFC/00326`) |
| `name` | company name |
| `icv_totalscore` | Total ICV Score %, a formula: `min(100, icv + manufacturer + bonus + transitional)` |
| `icv_mainactivity` | industry (Engineering and Construction, Manufacturing, IT, …) |
| `icv_latestcertificate` | date of the current certificate — **the default sort key, DESC** |
| `icv_icvscoreexpirydate` | grace-period expiry date |
| `statuscode` (on the linked certificate) | Draft / Pending Supplier Confirmation / Pending Certifier Confirmation / **Valid** / Under Re-assessment / … |
| `icv_companynamecrsearch` | name+CR concatenated; this is what the portal's own search box matches |

### Three measured facts that shape everything below

**a. The universe is about 7,100 companies, not the 5,000 the page claims.**
The UI says "5000 records found" and stops at page 250, and the service returns
`ItemCount: 5000` — but paging past 250 keeps returning real, distinct records.
Sweeping to exhaustion lands at roughly 7,100 (7,072 measured at pageSize 200,
7,122 at pageSize 250; the two disagree because page-number jumping without the
paging cookie is not stable in Dataverse). **So: always page with
`NextPagePagingCookie`, never by page number, and never trust `ItemCount`.**

**b. The whole listing costs about 36 requests.** 7,100 records at pageSize 200.
That is under a minute at a polite delay. This is the single biggest difference
from Monaqasat, where the listing alone is 1,200+ HTML pages and had to be
budgeted, sliced and rolled. Here there is no need for watermarks, rolling
passes or a listing/detail time split: **sweep the entire listing on every run.**

**c. The score history is already published, per company, back to inception.**
`/supplier-score-history/?id=<GUID>&status=<code>` carries a grid of
**ICV Score (%), Posted Date, Reason, Change in Score**, plus the company's
List of Services and List of Goods. A real example:

| Score | Posted | Reason | Change |
|---|---|---|---|
| 18.97 | 24/09/2026 | Certificate Renewed | −0.16 |
| 19.13 | 01/08/2025 | Sub Supplier Score Updated | +0.22 |
| 18.91 | 26/06/2025 | Certificate Renewed | +1.06 |
| 17.85 | 23/11/2024 | Transitional Percentage Added | +7.00 |

This is why the design is a mirror and not a lookup. We do not have to spend
two years accumulating a panel: **one backfill reconstructs the entire history
of every company today.**

## 2. The decision, and why

**Mirror the whole portal. Do not look the score up when an application
arrives.** An application-time lookup returns *today's* score, which quietly
ruins the three things the score is for:

1. **Point-in-time correctness.** Scoring a 2024 default with a 2026 ICV score
   is look-ahead bias. IFRS 9 and PD work needs the value as at the observation
   date, and the published history gives exactly that.
2. **A control population.** The ~6,000 companies that are not our customers
   are the sector medians and the peer distribution. "24%" means nothing;
   "24% against a Manufacturing median of 38%" is a feature.
3. **No external site in the credit path.** A loan application must not stall
   because Tawteen is down or changed its markup.

A live lookup stays, as a *fallback* for an applicant we have never seen, not
as a second system: `refresh --cr <n>` runs the same parse and writes the same
rows.

## 3. The store

SQLite, one file (`icv.db`), same conventions as `monaqasat` — GUID keys,
`first_seen`/`last_seen`, compressed stored responses, a `run` table.

```sql
-- One row per company as the portal knows it.
CREATE TABLE company (
    id          TEXT PRIMARY KEY,     -- accountid GUID; stable across renames
    cr_number   TEXT,                 -- as printed: '185319', 'QFC/00326'
    cr_root     TEXT,                 -- normalised for joining
    name        TEXT NOT NULL,
    name_norm   TEXT,                 -- the monaqasat normalisation
    industry    TEXT,
    first_seen  TEXT NOT NULL,        -- run date that first listed it
    last_seen   TEXT NOT NULL,
    gone_at     TEXT                  -- set after two clean sweeps without it
);

-- The published score series. Append-only; a row is never edited.
-- seq counts from the OLDEST row (1, 2, 3 …) so it is stable when new
-- observations arrive at the top.
CREATE TABLE score_observation (
    company_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    posted_date TEXT NOT NULL,        -- ISO
    score       REAL NOT NULL,
    reason      TEXT,                 -- 'Certificate Renewed', 'Transitional Percentage Added', …
    change      REAL,
    first_seen  TEXT NOT NULL,        -- run that first read this row
    PRIMARY KEY (company_id, seq)
);

-- Status and expiry exist ONLY in the listing, never in the history.
-- Type-2: a row per distinct state, closed when the state changes.
CREATE TABLE listing_state (
    company_id   TEXT NOT NULL,
    from_date    TEXT NOT NULL,       -- first sweep that saw this exact state
    to_date      TEXT,                -- last sweep that saw it; NULL = current
    total_score  REAL,
    status       TEXT,
    status_code  INTEGER,
    cert_date    TEXT,                -- icv_latestcertificate
    expiry_date  TEXT,                -- icv_icvscoreexpirydate
    manufacturer INTEGER,
    PRIMARY KEY (company_id, from_date)
);

-- What the company sells. Cheap at detail-fetch time, and the basis for
-- sector-concentration features.
CREATE TABLE company_service (
    company_id TEXT NOT NULL, service_type TEXT NOT NULL,
    service_type_ar TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY (company_id, service_type));

CREATE TABLE company_good (
    company_id TEXT NOT NULL, category TEXT NOT NULL, sub_category TEXT NOT NULL,
    category_ar TEXT, sub_category_ar TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    PRIMARY KEY (company_id, category, sub_category));

-- As in monaqasat: the evidence, compressed with a per-kind zlib dictionary.
CREATE TABLE raw_page (url TEXT, kind TEXT, fetched_at TEXT,
                       body BLOB, comp TEXT, sha256 TEXT);

CREATE TABLE run (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
                  mode TEXT, listed INTEGER, details INTEGER, new_scores INTEGER);

CREATE TABLE crawl_state (key TEXT PRIMARY KEY, value TEXT);

-- Never goes into a packed or shared copy. Same rule as monaqasat.
CREATE TABLE match (customer_id TEXT, company_id TEXT, method TEXT,
                    matched_on TEXT, confidence REAL, run_id INTEGER);
```

### Reading it point-in-time

```sql
-- score as at :asof
SELECT score, posted_date, reason FROM score_observation
 WHERE company_id = :id AND posted_date <= :asof
 ORDER BY posted_date DESC LIMIT 1;

-- status as at :asof
SELECT status, expiry_date FROM listing_state
 WHERE company_id = :id AND from_date <= :asof
   AND (to_date IS NULL OR to_date >= :asof);
```

**Score history is retrospective and free. Status history is prospective
only** — `listing_state` starts the day we start sweeping. That is the argument
for starting the monthly sweep now, before any model is written.

## 4. How a run works

**Backfill, once.** Full listing sweep (~36 requests), then every company's
detail page: ~7,100 fetches, about three hours at a 1.5 s delay. Comes back
with the complete score history of every ICV-certified company in Qatar.

**Steady state, monthly.** Full listing sweep again — it is a minute, so there
is nothing to be clever about. Then fetch a detail page only where the listing
moved: `total_score`, `status`, `cert_date` or `expiry_date` changed, or the
GUID is new, or its history has never been read. Expect a few hundred detail
pages a month, so a run is minutes, not hours.

A company that disappears from two consecutive clean sweeps gets `gone_at` set;
its rows are kept. Disappearing is itself information.

**Live fallback.** `refresh --cr 29309` puts the CR in the endpoint's `search`
(it matches `icv_companynamecrsearch`), takes the one record, fetches its
detail page, and writes the same rows as a sweep would. Used when an applicant
has no row, or when the mirror is older than a chosen number of days.

### CLI, mirroring monaqasat so the two feel the same

```
python -m icv crawl [--mode backfill|update] [--max-hours H]
python -m icv refresh --cr <n> | --name "<name>"
python -m icv status
python -m icv stats
python -m icv match --customers customer-data\customers.csv --out matches.csv
python -m icv export --table companies|scores|listing|matches --out f.csv
python -m icv pack   # excludes raw_page and match, as in monaqasat
python -m icv doctor
```

## 5. Matching to the customer book

Same ladder as Monaqasat, copied into this package for now and factored out
once two consumers have proved what they share:

`cr` → `name_exact` → `name_fuzzy` (token_sort ≥ 0.92, same script) →
`name_translit` (consonant skeletons ≥ 0.90, cross-script).

One extension: `cr_root` must learn the QFC form (`QFC/00326`), which Monaqasat
never saw. Qatar Financial Centre registrations are not in the same numeric
space as mainland CRs and must not be stripped to `326`.

As in Monaqasat, one customer may hold several rows in the book — a changed
registration, a subsidiary, a branch — and every row is matched on its own with
the results filed under the one customer id.

## 6. What to be careful about when modelling

- **Separate policy from performance.** `Transitional Percentage Added +7.00`
  is a rule change, not a company improving its local content. Feed raw deltas
  into a PD model and it will learn a regulation. Keep `reason` on every
  observation and derive an organic-movement series beside the headline score.
- **The headline score is a capped sum**, `min(100, icv + manufacturer + bonus
  + transitional)`. A company at 100.00 may be well past 100 underneath, so the
  top of the range is censored — treat it as such.
- **Lapse and grace are signals.** A certificate allowed to expire, or a move
  to Grace Period or Under Re-assessment, speaks to a borrower's ability to keep
  winning sector work. It lives only in `listing_state`.
- **Sector medians move.** Compare a borrower to its `industry` cohort at the
  same date, not to a fixed threshold.

## 7. Open before the first backfill

- Confirm the exhaustive count by sweeping once with the paging cookie and
  counting distinct `accountid`s — the 7,072 / 7,122 disagreement above is an
  artefact of page-number jumping and should resolve to one number.
- Decide whether to keep raw JSON responses or only the parsed rows. Monaqasat
  keeps pages because the evidence mattered for tender amounts; here the JSON is
  bulky and largely Dataverse metadata. Storing the detail HTML and a slimmed
  record of each listing response is probably the right balance.
- The portal has a date picker on the listing whose purpose is not yet
  established — it did not retro-date scores in testing. Worth one more look:
  if it does serve the list as at a past date, the `listing_state` history
  becomes retrospective too.
