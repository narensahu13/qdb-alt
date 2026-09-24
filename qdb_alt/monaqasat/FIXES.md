# Fixes — 22 September 2026

Branch `claude-monaqasat-fixes`, based on `main` at `64ddadf`. Package version
0.3.0 → 0.4.0.

This file is for whoever works on this code next, person or agent. For each
problem it gives: what went wrong, **how it was shown to be real** (a
reproduction, not a reading of the code), the cause, the fix, and the tests
that now guard it.

**Evidence, in one line:** 76 tests were added. When they are run against
`64ddadf`, 69 fail or error. On this branch all 219 tests pass
(`python -m unittest discover -s tests -t .`, about a minute, fully offline).
The 7 new tests that already passed on `64ddadf` cover behaviour that was
right and must stay right: an opened tender never moves back, same-day
awards, `--quick`, and a few others.

| # | Problem | Severity | Shown by |
|---|---|---|---|
| 1 | Name matching linked borrowers to unrelated companies, and was very slow | critical | 60 customers not on the site: 13,837 matches, 55 of the 60 matched |
| 2 | CR numbers in common Excel formats joined the wrong company, or none | critical | `"29,309"` became CR `29`, reported as an exact CR match |
| 3 | Tenders that moved section were missed, so their state went stale | high | a tender moved Available → Closed stayed "published" for good |
| 4 | Detail-page order and refetch rules: double fetches, wrong priority, awards without winners marked done | medium | 15 of 40 companies pages fetched twice |
| 5 | A few home-page answers stopped the detail queue for five nights | medium | six missing pages → no award rows stored on nights 1–5 |
| 6 | Feature table: award value took the smallest line; pending bids counted as losses | high for modelling | two lines of 600k + 400k → 400k; 1 win + 3 pending → win rate 0.25 |
| 7 | Classified register never refreshed or noticed removals unless run with `--full` | medium | by design of the old update mode |
| 8 | Register page 2 `ReadTimeout`; no fresh connection on retry; no UTF-8 guard | medium | page 2 takes 30.8 s cold against a 30 s timeout (the original error) |
| 9 | Customer rows with only an Arabic name were dropped; the load report did not say what could be matched | medium | a row with `name_ar` only was skipped as empty |
| 10 | Smaller: migration labels, v0.2 sighting kinds, scheduled-task claim, `harvest.ps1` could hang | low | `harvest.ps1` hangs on 200 KB of stderr (checked with PowerShell 7.4) |

---

## Site behaviour the fixes rely on

These were checked on the live site between 20 and 22 Sept 2026. If the site
changes, check these first.

- **Section order.** Awarded is newest award date first. Available and Closed
  are in *publish* date order; Closed is *not* in closing-date order.
  Cancelled is in no date order. Technically and Financially Opened are in
  neither publish-date nor any visible date order: page 1 mixed 2026, 2025 and
  2018 tenders.
- **Sections overlap.** Every Financially Opened tender is also listed under
  Technically Opened (for example, ids 654538 and 653565 appeared in both on
  22 Sept).
- **Companies pages grow as a tender advances.** Eight tenders in the
  Financially Opened section showed only the technical table: names, no
  amounts, and one showed no table at all. Award pages show the awarded
  table, every bidder, and the prices. For example, 658526 lists 7 technically
  opened bidders and 7 financially opened bidders with amounts. So replacing a
  tender's company rows with its latest page loses nothing, and per-stage
  snapshots are not needed (see "Not done").
- **Arabic names appear on the English pages** for some companies, for example
  WOQOD as `قطر للوقود (وقود)` on tender 3217/2022 (fixture
  `detail_534222_direct_agreement.html`).
- **Out-of-range page numbers return the last page.** An unknown tender id
  returns the home page with HTTP 200 (fixture
  `detail_600000_unknown_id_home.html`).
- **Timings.** Register page 2 has 145 activity rows (154 KB) and took 30.8 s
  with a cold cache; pages 1 and 3 took about 5 s. Page counts on 21 Sept:
  Awarded 1,210, Cancelled 225, Closed 127, Technical 55, Financial 21,
  Available 12, and the Bids sections 60 between them.

---

## 1. Name matching attached borrowers to unrelated companies

**What happened.** Two rules added in `abd4b46` made names match when only
fragments agreed:

- `match_customers` forced the score to at least 0.92 (usually 1.0) whenever
  the first words of the two names had the same consonants:
  `score = max(score, similarity(first, c0), 0.92)`. "Qatar Packaging Co"
  therefore matched every `QATAR …` company at 1.0, and "Gulf Packaging"
  matched Gulf Warehousing and Gulf Drilling.
- `match_keys` put consonant skeletons into the *exact* key set, so equal
  skeletons were exact matches. "Nasser Trading" = "Nisr Trading",
  "Al Amin Trading" = "Amana Trading" and "Doha Trading" = "Duha Trading", all
  as `name_exact` 1.0.
- Arabic names inherited the same problem: `قطر للتغليف` (Qatar Packaging)
  matched every Qatar company, WOQOD included.
- Every customer was compared with every indexed key, and a skeleton was
  recomputed for each pair.

**Shown by.** Two tests on synthetic names.

- **False matches.** 6,000 distinct company names on the site and 60 customer
  names that are *not* on it, so every match is wrong. `64ddadf` produced
  13,837 matches and matched 55 of the 60 customers, many at score 1.0. v0.3
  produced 18. This branch produces 8. Those 8 are near-identical names such
  as "Amana Alpha Travel" and "Al Mana Alpha Travel" (0.97), and they are
  labelled `name_fuzzy`.
- **Speed.** 200 customers against 20,000 company rows took 745 ms per
  customer on `64ddadf` and takes 8 ms per customer on this branch.

**Fix** (`normalize.py`, `match.py`):

- `match_keys()` returns exact keys only: the normalised name and the
  sorted-word key, in the name's own script.
- The fuzzy scorer is **token-sort**, threshold 0.92, within one script.
  token-set was dropped because it scores 1.0 whenever one name's words are a
  subset of the other's.
- Candidates are the names that share one of the customer's two rarest words.
  This is an inverted index built once in `NameIndex`, with no pairwise scan.
- Consonant skeletons are used **only across scripts** (Arabic ↔ Latin). They
  cover the whole name, need at least 6 letters (`MIN_SKELETON`) and a ratio
  of at least 0.90. `y` and `w` are kept: they are ي and و, real letters, and
  dropping them caused collisions.
- Cross-script matches have their own method, `name_translit`. An Arabic name
  gets two renderings: translated generic words for English-style names
  ("AL RAYYAN TRADING"), and letter by letter for machine transliterations
  ("Kyrwy Llmqawlat…").
- Arabic-script names now normalise to a folded Arabic key
  (`normalize_arabic`). Before, they normalised to `''`, so every Arabic-named
  company shared one key.
- Each company row keeps only its best evidence: `cr`, then `name_exact`,
  then `name_fuzzy`, then `name_translit`.

**Behaviour changes for callers:**

- There is a new method, `name_translit`, and a new flag,
  `match --translit` (default 0.90; 1 turns it off).
- `--fuzzy` now defaults to 0.92 and applies to token-sort.
- `match` now replaces the stored matches of the customers in the file,
  instead of adding to them; `--replace` still clears all matches.
- `summarise()` has a new `needs review` count.

**Two existing tests changed their expectation. Neither was weakened:**

- `test_match.ArabicNames.test_arabic_customer_matches_latin_site_name` now
  expects `name_translit` instead of `name_exact`/`name_fuzzy`. A cross-script
  match is labelled as one, so it can be reviewed separately.
- `test_normalize.Arabic.test_kyrwy_stem` now uses `كيروي` instead of `كروي`.
  "Kyrwy" is the site's letter-by-letter rendering of ك-ي-ر-و-ي. `كروي` is a
  different, four-letter word, and the old test only passed because `y` was
  dropped from skeletons, which is the rule that made "Nasser" equal "Nisr".
  The full-name case with `كروي` (`test_machine_transliteration_kyrwy`) still
  passes unchanged, at 0.96.

**Tests.** `test_match`: `NoFalsePositives`, `TrueMatchesStillWork`,
`Classified`. `test_normalize`: `ArabicScript`.

## 2. CR numbers

**What happened.** `cr_root` had returned to `re.match(r"\s*(\d+)")`:

| Input | Result on `64ddadf` | Problem |
|---|---|---|
| `"29,309"` | `"29"` | Excel writes a formatted number this way to CSV. The result is a *different company*, matched as `cr` 1.0 |
| `"CR-29309"` | `None` | no match |
| `"029309"` | `"029309"` | never equal to the site's `29309` |
| `"٢٩٣٠٩"` | the Arabic-Indic digits themselves | never equal |

**Fix.** `normalize.cr_root` accepts any prefix, leading zeros, thousands
separators, Excel floats (`29309.0`, `NaN`) and Arabic-Indic or Persian digits.
The migration recomputes every stored `cr_root`.

**Tests.** `test_normalize.CrNumbers`;
`test_match.TrueMatchesStillWork.test_cr_in_excel_formats` and
`test_thousands_separator_is_not_a_short_cr`.

## 3. Tenders moving between sections

This answers the question raised on 21 Sept: *"if a tender was open earlier
and is now closed, and we saved it as open, do we delete it from the open
table?"*

**What happened.** Every section was updated the same way: read from page 1
until two pages in a row held nothing new. That works only where a tender
entering the section lands on page 1, which on this site is Awarded alone
(sorted by award date). A tender that closes lands in Closed where its
*publish* date puts it. A cancellation lands anywhere in Cancelled. Neither is
ever reached from the top, so the tender kept its old state for good. Nothing
recorded that a tender had left a section. The README's claim — "any tender
that moved (open last month, awarded this month) is … updated in place" —
held only for moves into Awarded.

**Shown by.** A simulated site with 40 tenders in Available and 200 in
Closed. One tender moves to Closed and lands on page 4. The update run read
Closed pages 1–2, and the tender stayed `published`
(`test_lifecycle.OpenToClosed`).

**Fix** (`cli._walk_listings`, `store`). There is one policy per section:

- **Awarded** gets a head scan that stops only when two pages hold nothing
  new **and** it has passed the newest award date already held minus
  `--lookback-days` (14). Awards are sometimes published with an earlier date.
  On top of that, it gets a **rolling full pass**: `--roll-pages` (100) per
  run, restarting `--sweep-days` (7) after the last pass finished.
- **Cancelled** gets the same, without a date floor.
- **Every other section** (about 275 pages in total) is **read end to end
  every run**.

Around those policies:

- **Nothing is deleted.** There is one `tender` row per tender. `sighting` has
  one row per (tender, section) with `first_seen` and `last_seen`, and now
  `gone_at`.
- **`gone_at` needs two misses.** A tender is marked `gone_at` after **two**
  complete, clean full reads in a row that did not list it. One miss is not
  enough because a tender leaving a list mid-read shifts the rest up a place,
  and one can cross a page boundary unseen. Seeing it again clears the mark.
  A read with any failed page marks nothing.
- **`tender.state` = the most advanced section the tender is listed in now**
  (`Store.refresh_state`). It is re-derived after every listing page, and
  after gone marking.
  - Overlapping sections resolve to the furthest one.
  - State moves backwards only before opening (future, published, closed): a
    closing date extended after closing puts a tender back in Available.
  - Once opened, a tender never goes back.
  - A tender listed nowhere keeps its last state and is reported
    (`status`, `Store.left_every_list`) until the rolling pass places it.
- **Listings from the crawl never write `state`.** `_absorb` strips it and
  calls `refresh_state`. `upsert_tender` still accepts an explicit state
  (forward only) for direct callers and tests.
- **`--quick`** is the old behaviour — head scans only — for when speed
  matters more than completeness.
- **`history --tender ID`** prints one tender's path: sections, first/last
  seen, gone, fetches and companies.
- **Per-run cost** is about 275 pages of full reads plus the head scans, and
  up to 200 more while a rolling pass is under way. That is roughly 12–25
  minutes at the default 1.5 s delay, before detail pages.

**Tests.** `test_lifecycle`:

- `OpenToClosed`, `Reopened`, `OverlappingSections`
- `AwardedHeadScan`: an award dated inside the lookback, an award dated
  beyond it, rolling passes, and a same-day block with interleaved new awards
- `CancelledIsNotInDateOrder`, `GoneMarking`

The simulated site in `test_incremental.FakeSite` now gives each tender an
award date (one a day, in id order, like the live list) and serves companies
pages the way the site does: names only until awarded.

## 4. Detail pages: order and refetch rules

**What happened.**

- **Double fetches.** Each section fetched its own detail pages straight
  after its own listings. A tender in both Technically and Financially Opened
  was fetched as `technical`, then again as `financial` once the second
  listing was read: 15 of 40 companies pages in the simulation.
- **Wrong priority.** TenderDetails pages came before companies pages, which
  carry the winners, bidders and prices.
- **Awards marked done too early.** A tender listed as awarded whose page did
  not name a winner yet was marked fetched at `awarded`, and never read again.

**Fix** (`cli._fetch_details`, `Store.detail_jobs`):

- All listings are read first, then one detail queue runs, most valuable
  first:
  1. companies pages of awarded tenders not read since the award
  2. companies pages of tenders in opening that were never read
  3. TenderDetails pages
- A companies page is re-read only when the tender reaches `awarded`,
  because at financial opening the page still shows the technical table only.
- **Awarded with no winner on the page** keeps what the page shows, is not
  marked done, and is retried, up to 5 times.
- **Failure keys.** Companies-page failures are keyed `id@state`, so a page
  that failed before the award gets a fresh start after it.

**Tests.** `test_lifecycle.OverlappingSections`,
`DetailPages.test_companies_pages_come_before_tender_details`,
`DetailPages.test_awarded_but_no_winner_on_the_page_yet`.

## 5. Home-page answers stalled the detail queue

**What happened.** When the site answered a detail request with its home page
(`found` is False), the page was recorded as a failure **and** counted toward
the five-consecutive-failures stop. Five such pages at the front of the queue
ended the detail phase before a single companies page was read. The next
night, the same pages came first again. They dropped out only after five
attempts each.

**Shown by.** 60 awarded tenders, six of them answering with the home page.
Award rows stored: 0, 0, 0, 0, 0, then 60 on night 6
(`test_lifecycle.DetailPages.test_home_page_answers_do_not_stall_the_queue`
now expects 54 on night 1).

**Fix.** A home-page answer is recorded as `missing:<page type>`. It does not
touch the consecutive-failure counter, which now counts network failures
only. It is retried weekly, three times at most.

## 6. Feature table (`features.py`, `FEATURE_VERSION` v1 → v2)

- **Award value.** Per tender, v1 took the *smallest* of a company's rows,
  which could be a proposal amount. A company awarded two lines, 600,000 and
  400,000, was credited 400,000. v2 sums the company's Approved Value lines.
- **Win rate.** v1 divided wins by every tender the company appeared in, so a
  bid still under evaluation counted as a loss: 1 win and 3 pending bids gave
  0.25. v2 divides by *decided* tenders — those whose winners are known — and
  needs at least 3. There is a new column, `decided_12m_count`.
  `bids_12m_count` keeps its meaning (every appearance).
- `analyse` and `profile` use the same won / lost / open split.

**Tests.** `test_features.FeatureFixes`; `test_cli.Reports`.

## 7. Classified register: refresh and removals

**What happened.** An update read only the tail of the register, where new
companies land. Evaluations, certificate dates and activities changed on the
site but not in the store unless someone ran `--full`, and a company removed
from the register was never noticed.

**Fix** (`cli.cmd_companies`). Each run also reads a slice of a rolling
refresh pass: `--register-pages` (20) per run, a new pass every
`--register-days` (30). A company missing from two complete passes in a row
gets `delisted_at`.

- **Holes.** A pass with a page left failed stays open until that page is
  retried, so a hole cannot make a whole page of companies "missing".
- **Re-parsing.** `parse` of stored HTML never changes delisting state:
  `upsert_company` without a `pass_id` leaves it as it was.

**Tests.** `test_register`.

## 8. Timeouts and encoding (`fetch.py`)

- **Timeout.** The `ReadTimeout` on register page 2 came from a page that
  takes 30.8 s cold against a 30 s timeout. A browser showing it in 5 s is
  usually showing a cached copy. The default read timeout is now 120 s
  (`--timeout`).
- **Fresh connection on retry.** After a timeout or connection error, the
  retry uses a new session (`Fetcher._rebuild`), with the same TLS setting and
  the English-culture cookie, instead of the pooled keep-alive socket.
- **UTF-8 guard.** `decode_body()` decodes as UTF-8 unless the server names a
  charset. requests otherwise falls back to ISO-8859-1 for `text/html` and
  would mangle every Arabic character.

**Tests.** `test_fetch.Resilience`, `test_fetch.Decoding`.

## 9. Customer file

- **Arabic-only rows kept.** A row with only an Arabic name, in the `name_ar`
  column and no CR, was skipped as empty. It is now kept.
- **Load report.** It now says how each customer can be joined:
  - with a CR number: joined exactly
  - no CR, English name: joined by name, and similar-name matches need review
  - no CR, Arabic name only: transliteration only, so review, or add the CR
  - a CR value with no number in it is listed as a warning

**Tests.** `test_loader.WhatCanBeMatched`.

## 10. Smaller fixes

- **Migration** (`Store._migrate`, `PRAGMA user_version` = 2). Every step
  checks before it acts, so reopening changes nothing. On an existing
  database it:
  - adds the new columns;
  - recomputes `cr_root` and `name_normalised`;
  - relabels `stage='bidder'`, which the first stage migration copied from
    `role`, as `unknown` (`parse` restores the exact table from stored HTML);
  - re-keys sightings that an older backfill had filed under non-existent
    sections (`published`, or a bid's plain `awarded`);
  - drops per-section detail-failure keys, whose pages are retried under the
    new keys.

  Tested against both schemas real databases were written with:
  `fixtures/schema_v0.3.sql` and `fixtures/schema_qdb-alt_2026-09-21.sql`
  (`test_migration`).

  **What an upgraded database does on its first runs:**
  - Awarded and Cancelled have no `last_full_pass` recorded, so a rolling
    pass starts at once. That is a one-time re-read, which catches anything
    the old head scans missed.
  - The first two full reads of the other sections mark tenders that are no
    longer listed.
- **`install-task.ps1`** claimed that running it elevated lets the task run
  while signed out. It registers no logon principal, so the task runs only
  while the user is signed in; a locked screen is fine. The comment now says
  so.
- **`harvest.ps1`** read stderr only after stdout had ended. If Python writes
  more than a pipe buffer to stderr, as a long traceback does, both processes
  wait forever. Checked with PowerShell 7.4: the old function hangs on
  200 KB of stderr, and the new one, which drains stderr asynchronously,
  returns with the exit code.

## Not done, on purpose

- **Per-stage company snapshots** were planned on 21 Sept to keep bidders
  that disappear at award. The live check above showed that award pages carry
  every bidder and the prices, so `replace_companies` replacing a tender's
  rows with its latest page is correct as it is.
- **Matching on the secondary number** (the value after `|` in
  `29309/1 | 10497`) was not added. Both numbers are in the same numeric
  range, so one company's secondary number often equals another's CR.
- **Compressing stored HTML** was not done here. It was done the next day;
  see the follow-up at the end of this file.
- **Fuzzy matches are never treated as confirmed.** `profile` marks them with
  `*`, and `match` prints how many need review.

## Checking it yourself

```powershell
cd qdb_alt\monaqasat
python -m unittest discover -s tests -t .      # 240 tests, offline
python -m monaqasat crawl --kind all --max-hours 1
python -m monaqasat status                    # listed / left / last full read / rolling pass
python -m monaqasat history --tender 659460
python -m monaqasat match --customers book.xlsx --out matches.csv
```

---

# Follow-up, 23 September 2026 (v0.5.0)

Three changes, after rehearsing the question they came from: *next month I
run it again -- does it only fetch what changed, does it notice the bids
that closed, and can the database stop growing like this?*

The rehearsal is the evidence for the first one, so it comes first.

## The rehearsal

The real database as it stood on 22 Sept (25,300 tenders, 4,781 classified
companies) against a simulated site holding those tenders plus the sections
never crawled yet, at the page counts the live site has. The actual crawl
code runs against it; the only thing faked is the HTTP. Requests are counted
and converted at the 1.5 s delay.

| Run | Listing pages | Companies pages | TenderDetails | Total | At 1.5 s |
|---|---|---|---|---|---|
| Catch-up: first run of the fixed code | 764 | 25,000 | 11,941 | 37,705 | ~16 h |
| A month later | 1,490 | 444 | 496 | 2,430 | ~61 min |
| The next night, nothing changed | 312 | 0 | 0 | 312 | ~8 min |
| A month after that | 1,766 | 444 | 496 | 2,706 | ~68 min |

The catch-up run is large because the September crawl fetched no companies
pages at all for the 24,200 awarded tenders (that was fix 4: TenderDetails
ran first and used the whole budget), and because Available, Closed,
Cancelled, Future and the seven Bids sections have never been read. It is a
one-off; every run after it costs about an hour a month, or minutes a night.

What the update run was checked to do, in the same rehearsal:

- open bids from last month that were decided now read as awarded, their
  companies page re-read exactly once, award rows stored
- tenders that closed found in Closed at page 4, where their publish date
  puts them
- tenders that reached financial opening moved on, with no wasted re-fetch
- an award published this month but dated 40 days back: found
- five cancellations dropped on page 100 of Cancelled: found
- detail pages fetched only for what changed (444 companies pages for 418
  newly awarded tenders)
- nothing deleted, and the second month costs the same as the first

## 11. The rolling pass now fits how often you run it

**What happened.** `--roll-pages` was a fixed 100 pages per run. That suits a
nightly task: Awarded (1,210 pages) is swept every fortnight. Run the same
command once a month and the pass advances 100 pages a *month* -- a full
sweep of Awarded takes a year, so a cancellation deep in the list or an award
published with an old date can sit unnoticed for months. Nothing was wrong
with the code; the default assumed a frequency.

**Shown by.** The same monthly run, capped the old way at 100 pages, costs
1,433 requests (~36 min) and leaves the Awarded pass at **page 102 of
1,273** -- twelve more monthly runs, a year, before the section has been
swept once, and Cancelled at page 102 of 226. Sized from the gap instead,
the run costs 2,430 requests (~61 min) and both passes finish inside it. An
hour a month, and nothing waits a year.

**Fix.** The slice is sized from the gap since that command last ran:
`pages x (days since last run) / --sweep-days`, never fewer than 100 pages.
Nightly runs take a seventh of the section each night; a run a month later
sweeps the whole section in that run. `--roll-pages` now *caps* a run for
anyone who wants a hard ceiling. The register refresh is sized the same way
against `--register-days`, so a monthly run refreshes all 245 pages (about
six minutes) instead of 20.

**Tests.** `test_lifecycle.RunFrequency`, `test_register.RegisterFrequency`.

## 12. The database is about a tenth of the size

**What happened.** The September crawl left a 1 GB file, and it was only
part way through: every page is stored as HTML, and a full load is a few GB.
Nearly all of that is the same navigation, scripts and styles repeated on
every page.

**Fix** (`store.py`, schema v3). Pages are compressed as they are stored.
One page of each kind is kept in the new `blob_dict` table and used as a
zlib preset dictionary for the rest, which is what makes the repeated
chrome nearly free. Nothing is lost: `html_of(row)` returns the page exactly
as fetched, and `parse` re-parses from it as before.

**Measured** on the pages captured from the live site in `fixtures/`:

| | Raw | Stored | |
|---|---|---|---|
| A detail page | 24.3 KB | 0.4 KB | 57x |
| Another detail page | 29.8 KB | 0.9 KB | 35x |
| A home-page answer | 43.8 KB | 5.9 KB | 7x |
| A listing page | 5.8 KB | 0.8 KB | 7x |
| All eight fixtures | 137.4 KB | 10.7 KB | 13x |

Detail pages compress hardest because they are the ones that share their
layout with the sample. Expect something around ten times on the real
database; `compact` prints what it actually achieved.

**For a database written before this**, nothing happens on open -- a 1 GB
file would take minutes to rewrite, and that is not something an ordinary
`status` should do. The work is a command:

```powershell
python -m monaqasat compact        # compress what is stored, then VACUUM
```

`--no-vacuum` skips reclaiming the space (VACUUM needs room for a second
copy of the file while it runs). `--drop-before DATE` additionally drops the
stored copy of older pages, keeping their parsed rows and content hashes;
you rarely want it, since compression already makes the file small.

**Tests.** `test_storage`: round-trip including Arabic, pages written before
v3 still readable, `compact` keeping every page byte for byte, re-parsing
after compacting, and a packed copy's contents.

## 13. A packed copy no longer carries your customers

**What happened.** `pack` (added 22 Sept, to move a database between
machines) copied every table except `raw_page` -- including `match`, which
holds customer ids, customer names and the CR they matched on. The packed
file in `data/monaqasat.slim.db` happens to be clean because `match` was
empty when it was made, but the next `pack` after a `match --customers` run
would have put the bank's book into a public repository.

**Fix.** `pack` leaves out `raw_page`, `blob_dict` and `match`, and says so
when it runs.

**Tests.** `test_storage.PackedCopy` fails if a customer id or name appears
anywhere in the packed file.

## Still not done

- **Trimming pages before storing them** (dropping scripts and styles) would
  save another two to three times on top of compression, but the stored page
  would no longer be the page as served. Compression gets most of the way
  there and keeps the evidence intact.
- **A second sample** when the site's layout changes: pages already stored
  keep the sample they were written against (that is why `blob_dict` rows
  are never deleted), and new pages would compress a little worse until one
  is added. Not worth the machinery today.

---

# Follow-up, 24 September 2026

One report from a real first run, and what it exposed.

```
$ python -m monaqasat crawl --kind awarded --max-hours 0.25
  ... 459 listing pages, 0 detail pages, 9,178 tenders, 0 award rows
$ python -m monaqasat match --customers sample\customers.csv --out matches.csv
No company rows yet. Run `crawl` first.
```

Nothing crashed, no page failed, the numbers all went up -- and the run was
useless, because everything a borrower is matched on lives on the per-tender
pages that were never reached. Four fixes: one real, three about the tool
saying what is happening.

## 14. A budgeted run no longer spends all of it on listing pages

**What happened.** `cmd_crawl` reads every listing page first, then fetches
detail pages with whatever time is left. That ordering is right -- a tender's
section has to be settled before its detail pages are worth fetching -- but
on a *first* load Awarded alone is 1,212 listing pages, about half an hour at
the 1.5 s delay. Any budget under that never reaches step 2. The store fills
with tenders that have no winners, no bidders and no prices, `match` has
nothing to join to, and no number in `stats` says so.

It is specific to the first load (or a re-crawl after a long gap): once the
sections are read, a nightly run spends a few minutes on listings and the
rest on details, which is why it was not seen in the rehearsal.

**Fix.** A run with `--max-hours` now splits it: `LISTING_SHARE = 0.6` to the
listing pass, the rest to the detail pages. `_expired(args, "listings")`
checks the earlier of the two deadlines, `_expired(args)` the run deadline,
so only the listing loops stop early. If the detail queue empties before the
clock does, the listing deadline is cleared and the listings carry on with
what is left; if the listings finish inside their share, the details get
everything that remains. Without `--max-hours` nothing changes.

A 15-minute run at the 1.5 s delay is about 600 requests: 459 listing pages
and nothing else before, roughly 360 listing and 240 detail pages now. The
second number is the one that matters -- 240 companies pages is 240 tenders
with winners and prices, on the first run instead of the fifth.

**Tests.** `tests/test_lifecycle.TimeBudgetSplit` runs a budgeted crawl
against a 2,000-tender fake site on a clock that only moves when a page is
fetched, so the split is measured in requests and not in wall time:

- a budgeted run always comes back with detail pages, and award rows in the
  store (this is the test that fails on the old code)
- the listings still get the larger share
- the next run carries the first load on from where it stopped
- a run with no budget is never cut short
- `--pages 1-<held>` spends the whole run on details

## 15. `match` now says which pages are missing

**What happened.** With tenders but no company rows, `match` printed
``No company rows yet. Run `crawl` first.`` -- which is what the person had
just done, twice.

**Fix.** When the store holds tenders but no company rows, it says that the
companies pages are the ones carrying winners, bidders and prices, that a
first load reads all the listing pages before any of them, and gives the
command that spends a run on details instead:

```
python -m monaqasat crawl --kind awarded --pages 1-<deepest page held> --max-hours 1
```

`crawl` prints the same thing at the end of a run that read no detail page
while some were queued. Both numbers come from the store, not from a guess.

## 16. The duplicate-customer-id warning was wrong

**What happened.** The load report warned `N duplicate customer id(s) --
later rows overwrite earlier ones in matches`. A bank's book has repeated
ids on purpose: a CR that changed, a subsidiary, a branch with its own
registration. The warning said that work was being thrown away.

It is not. `match_customers` walks every row and appends to the result, so
each row is matched on its own and the results are filed under the one id.
Two rows collide only when both land on the *same company row of the same
tender*, and there the stronger evidence wins -- a CR match over a name
match. In the book this came from, 1,433 rows over 1,032 ids, 343 customers
with more than one row.

**Fix.** It is now a note, not a warning, and it says what actually happens.
`print_load_report` prints warnings as `warning:` and notes as `note:` --
before, both printed as `note:`, so a real warning read like an aside.

**Tests.** `tests/test_match.OneCustomerSeveralRows`: every row of a customer
is matched, a branch CR (`29309/4`) reaches the parent, two rows on the same
company row keep the CR match, and the load report calls the repeated id a
note with an empty `warnings` list.

## 17. The real book can no longer be committed by accident

**What happened.** `sample/customers.csv` is a five-row example and is
tracked. A real book dropped at that path -- the obvious thing to do, since
it is the path in the README's example -- would be committed with the next
`git add`.

**Fix.** `/customer-data/` and `*.book.csv` are in `.gitignore`, and the
README says to keep the book in `customer-data\` or outside the tree, plus
why one customer often needs several rows. (Fix 13 already stopped `pack`
from copying the `match` table into a shareable database.)

## Checking it yourself

```powershell
cd qdb_alt\monaqasat
python -m unittest discover -s tests -t .     # 249 tests, offline
python -m unittest tests.test_lifecycle.TimeBudgetSplit -v
python -m unittest tests.test_match.OneCustomerSeveralRows -v
```

To watch fix 14 fail without it, set `LISTING_SHARE = 1.0` in `cli.py` (that
is the old behaviour: listings take the whole budget) and run
`TimeBudgetSplit`. Two of the five fail --
`test_a_budgeted_run_always_brings_back_some_detail_pages` with
`0 not greater than 0`, and `test_the_next_run_carries_the_first_load_on`
with `100 not greater than 100`, the second run having re-read the same
listing pages as the first. Put `0.6` back and all five pass.
