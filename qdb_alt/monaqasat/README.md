# monaqasat — Qatar state procurement harvester

Downloads the published record of Qatar state procurement from
`monaqasat.mof.gov.qa` — every tender and bid section, plus the register of
companies classified to bid — stores it locally, and joins it to your
customer book.

It answers, for any borrower:

- **Does it win government work?** How much, from whom, trending up or down.
- **Does it bid and lose?** The opened sections name everyone who bid, before
  the award. A borrower bidding steadily and never converting is a different
  risk from one that never bids at all — and you cannot see that from award
  data alone.
- **Is it allowed to bid?** The classified register gives company size, the
  government's own evaluation grade, classification expiry, and the activity
  codes it is graded for.

With several years of history from day one, which no other alternate-data
source gives you.

Standalone. Runs on a laptop. No API key, no bank infrastructure.

**What changed on 22 Sept 2026, and why:** [FIXES.md](FIXES.md) -- each
problem, how it was reproduced, the fix, and the test that guards it.

---

## Why a local harvest rather than a lookup per customer

Every corpus here is finite and enumerable — about 24,000 awarded tenders
across 1,210 pages, plus thirteen other sections and ~4,900 classified
companies across 244 pages. Downloading once and matching locally beats
searching the site per customer for four reasons.

- **History.** Awards are dated. One crawl gives you years of back-history.
  Per-customer lookup only ever gives you today, and a scored module will
  eventually need a time series.
- **Matching.** Their search box is not yours to tune. With a local copy you
  match on commercial registration number first and re-run the matcher across
  everything whenever you improve it.
- **Load.** One polite incremental crawl a day, not 20,000 searches.
- **Counterparties.** Your borrowers' buyers and suppliers match against the
  same corpus at no extra cost.

## The thing that makes this work

**Every company row carries its commercial registration number.** QDB already
holds the CR number for every borrower from onboarding, so matching is a join
on an identifier, not a guess at a name. Name matching is there as a fallback
and is reported separately, with its score, so you can trust the CR matches
and eyeball the rest.

The detail pages also list the companies that **bid and lost**, not only the
winner — a pipeline and competitiveness signal you cannot get anywhere else.

---

## Install

```bash
pip install requests beautifulsoup4 truststore
# optional, better fuzzy matching:
pip install rapidfuzz
```

`truststore` matters on a corporate network: it makes Python use the Windows
certificate store, which is where an inspection proxy's root certificate
lives. Without it Python fails where Chrome succeeds.

## If it will not connect

```bash
python -m monaqasat doctor
```

Run this if a command fails to connect. It tests each cause separately --
TCP reachability, four TLS handshakes, who signed the certificate, and HTTP
through `requests` with and without the system proxy -- then names the cause
and the fix.

Most of the time you will not need it, because the fetcher negotiates TLS
by itself.

### The TLS step-down

`monaqasat.mof.gov.qa` presents a valid DigiCert certificate but offers only
RSA key-exchange ciphers such as `AES128-GCM-SHA256`. OpenSSL 3 will not
negotiate those at its default security level, so the server finds no
acceptable cipher and closes the connection without sending an alert. Python
reports that as a bare `SSLZeroReturnError`, which looks alarming and is not.

The fetcher tries, in order, and keeps the first that works:

1. **default** -- OpenSSL 3 defaults
2. **system certificate store** -- via `truststore`, which is where a
   corporate root lives if your network inspects TLS
3. **compat** -- verified TLS at OpenSSL security level 1

Every command prints which one it settled on. Certificate verification is on
in all three; what step 3 gives up is forward secrecy, which is the server's
configuration choice. For reading public pages with no credentials in the
request that is an acceptable trade. It would not be for anything carrying
secrets, and nothing here does.

Pin one and skip the negotiation with `MONAQASAT_TLS=default|system|compat|certifi`.

| What doctor finds | Fix |
|---|---|
| Certificate signed by something that is not a public CA | `pip install truststore`, or get the root CA from IT and set `REQUESTS_CA_BUNDLE` |
| Direct works, proxy does not | `set NO_PROXY=monaqasat.mof.gov.qa`, or get the right proxy URL from IT |
| Every handshake closed, including unverified | See the verdict -- it is a network block or a server-side control, and neither is fixed with a cipher list |

Verification is never disabled. If anything suggests `verify=False`, ignore
it and send me the doctor output.

## Use

```bash
# 1. Look before you leap: robots, and the size of all fifteen sections.
python -m monaqasat probe

# 2. Start small: 5 pages of one section, with its detail pages.
python -m monaqasat crawl --kind awarded --pages 1-5

# 3. The register of classified companies — small, fast, high value.
python -m monaqasat companies

# 4. The sections that name companies: who bid and who won.
python -m monaqasat crawl --kind companies

# 5. Or everything, all fourteen sections. Resumable.
python -m monaqasat crawl --kind all

# 6. Join to your book, and look at the results.
python -m monaqasat match --customers sample/customers.csv --out matches.csv
python -m monaqasat analyse --min-bids 3
python -m monaqasat profile --customer C002
```

`--kind` takes a section name, or one of four shortcuts:

| Shortcut | Covers |
|---|---|
| `all` | all fourteen sections |
| `tenders` | the seven tender states (government buying) |
| `bids` | the seven bid states (government selling) |
| `companies` | only the six states that name companies — the best value per request |

Section names: `available`, `closed`, `technical`, `financial`, `awarded`,
`cancelled`, `future`, and the same seven prefixed `bids-`.

Other commands: `stats`, `export --table awards|tenders|companies|activities`,
`parse` (re-parse stored HTML without re-crawling).

**Your customer list** can be `.xlsx` or `.csv`. Give it straight from
Excel -- an `.xlsx` avoids encoding problems entirely:

```powershell
python -m monaqasat match --customers book.xlsx --sheet Customers
```

**Keep the real book out of this repository.** `sample/customers.csv` is a
five-row example and is committed; a real book put at that path would be
committed too. `customer-data/` and `*.book.csv` are ignored, so
`customer-data\book.xlsx` is a safe place for it, as is anywhere outside the
tree.

One customer often needs more than one row -- a CR that changed, a
subsidiary, a branch with its own registration. Give each its own row under
the same customer id: every row is matched, the results are filed under that
one id, and a branch number like `29309/4` reaches the parent anyway.

It needs a name column, a CR number column, or both; a customer id is
optional. Common header variants are recognised -- `CIF No`, `Customer Name`,
`C.R. No.`, `Commercial Registration Number` and so on -- and the command
prints which column it used for what, so a wrong guess is visible.

CSV files are read in whatever encoding Excel saved them. On Windows that is
usually *not* UTF-8 but the system code page -- Windows-1256 on an
Arabic-locale machine, Windows-1252 otherwise -- which is where a
`UnicodeDecodeError` comes from. The loader detects which it is. It also
catches the opposite case -- a UTF-8 file with a few stray bytes pasted in --
and keeps the Arabic intact instead of mangling every name to fix one byte.

The CR number is what makes matching reliable. Supply it wherever you have
it. It is read in any format it turns up in -- `29309/1`, `CR-29309`,
`029309`, `29,309` (Excel's thousands separator), `29309.0`, Arabic-Indic
digits -- and the load report shows how many customers have one.

Every match records how it was made:

| Method | Means | Use as is? |
|---|---|---|
| `cr` | same commercial registration number | yes |
| `name_exact` | same normalised name, same script (1.0), or same words in another order (0.99) | yes, mostly |
| `name_fuzzy` | similar name, same script (token-sort >= 0.92) | review |
| `name_translit` | an Arabic name against a Latin one, as consonant skeletons of the whole name (>= 0.90) | review |

Names on the site are English ("AL RAYYAN TRADING WLL"), a letter-by-letter
machine transliteration ("Kyrwy Llmqawlat Walnqlyat Walkhdmlt"), or --
sometimes, even on the English pages -- Arabic ("قطر للوقود (وقود)"). An
Arabic customer name is compared with all three: exactly against Arabic site
names, and by transliteration against the others. A customer with only an
Arabic name and no CR can therefore be matched, but only by
`name_translit`, which needs a look. `--fuzzy 1` and `--translit 1` switch
the similar-name methods off.

## Harvest without keeping the laptop open

A first load of every section is tens of thousands of requests (about a day
or two at the default delay). The harvest command stops cleanly when the
clock runs out and writes every page as it goes, so the next run continues.

```powershell
# From qdb_alt\monaqasat
python -m monaqasat harvest --max-hours 6
python -m monaqasat status
```

Register a nightly task that wakes the machine and keeps going until
`status` shows every section complete. After that, the same task keeps the
store current: new tenders, and every tender that moved section (open last
month, closed this week, awarded today) -- see "What an update run does"
below for how that is guaranteed rather than hoped for.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1
```

Closing the lid usually sleeps Windows, which stops the job. Set **When I
close the lid** to **Do nothing** while plugged in, or run the task on a VM
or always-on PC. The scheduled task is set to wake the computer; sleep still
wins if the lid-close action is Sleep.

Logs land in `logs/harvest-YYYY-MM-DD.log`.

## Running it periodically

Every crawl is incremental. Run the same command daily or weekly and it only
fetches what is new or has changed.

```powershell
python -m monaqasat harvest --max-hours 6          # everything, then incremental
python -m monaqasat crawl --kind all               # every tender and bid section
python -m monaqasat companies                      # the classified register
python -m monaqasat status                         # what's done, what's pending
python -m monaqasat history --tender 659460        # how one tender moved
```

### Why it tracks tenders, not pages

Every tender listing is newest-first. Each tender published this week pushes
every older tender further down, so page 90 today is about page 92 next week.
Tracking progress by page number would get this wrong both ways: page 1 would
count as "done" and the new tenders on it would be missed, or everything would
be downloaded again. So progress is tracked by **tender id and section**, and
listing pages are only read to find out what is new.

That matters because the costs are lopsided. One listing page is one request
and covers twenty tenders. Detail pages are up to two requests *per tender*.
A re-run reads a few listing pages and skips every detail page it already
holds.

### What an update run does

How a section is kept current depends on how the site orders it (checked on
the live site, Sept 2026):

| Section | Order on the site | Each run |
|---|---|---|
| Awarded | newest award date first | read from page 1 until two pages hold nothing new **and** the award dates are 14 days older than the newest one held (`--lookback-days`), plus a slice of a rolling full pass |
| Cancelled | no date order | read from page 1 until caught up, plus a slice of a rolling full pass |
| everything else | Available and Closed by *publish* date; Technically / Financially Opened in neither | read end to end -- about 275 pages across all of them |

Why: a tender that closes today lands in Closed where its *publish* date
puts it, which can be page 4, not page 1. Reading from the top until nothing
new would never get there, and the tender would stay "open" in the store
indefinitely. Reading the small sections end to end finds it wherever it
lands, and is also what lets a run notice that a tender has **left** a
section. The rolling pass re-reads Awarded and Cancelled end to end, so an
award published with an old date, or a cancellation on page 100, is found
even though no single run reads 1,400 pages.

**How much of that pass a run does depends on how long it has been since the
last one.** Run this nightly and each night takes a slice, finishing the
pass within `--sweep-days`. Run it once a month and that one run sweeps the
whole section, because there is no next night to carry it. You do not have
to configure anything for that; `--roll-pages` is there to cap a single run
if you want to.

Then, after every listing has been read:

1. **State.** A tender's state is the most advanced section it is listed in
   *now*. The site lists a tender in several sections at once -- every
   Financially Opened tender is also under Technically Opened -- so the
   furthest one wins. Nothing is deleted: when a tender moves from Available
   to Closed, it keeps its Available record, marked `gone_at` once two full
   reads in a row no longer list it (two, because a page that shifts during
   a read can hide a tender once). `history --tender ID` shows the whole
   path. A closing date extended after closing sends a tender back to
   Available; once opened, a tender never moves back.
2. **Detail pages**, most valuable first: companies pages of newly awarded
   tenders (winners, every bidder, prices), then companies pages of tenders
   in opening (who is bidding now), then TenderDetails. A companies page is
   read again only when the tender is awarded -- at financial opening the
   site still shows the technical list only, so re-reading it then adds
   nothing. A page that answers with the site's home page is recorded as
   missing and tried again weekly, three times; it does not stop the run.
3. **Gaps.** Any listing page that failed is re-read on the next run.

`--quick` skips the full reads and the rolling pass -- fast, but a tender
that moves into the middle of a list is not noticed until the next normal
run.

A section counts as complete only once it has been read to the last page
**and** no page is left failed. A load with a hole in it is not complete.

### The case of "run 100 pages when 90 are there"

`--pages 1-100` restricts the run to that range, and the update logic still
applies inside it. Having stored pages 1-90 earlier and published 200 tenders
since, re-running `--pages 1-100` fetches detail pages for those 200 only --
not the 2,000 already held. The tests check exactly this case.

### Options

| Flag | Effect |
|---|---|
| *(default)* | incremental, as above |
| `--full` | read every page of every selected section now (including all of Awarded). Still only fetches details that are missing or stale. |
| `--quick` | only read from the top until caught up, everywhere |
| `--pages 1-100` | stay within that range |
| `--limit-details 500` | cap detail requests this run -- spread a large first load over several days |
| `--stop-after 3` | be more cautious about deciding it has caught up |
| `--lookback-days 14` | how far below the newest award date the Awarded scan keeps reading |
| `--roll-pages 100` | cap the rolling pass at this many pages per run. Unset, the slice is sized from the gap since the last run, so the pass finishes within `--sweep-days` whether you run nightly or monthly |
| `--sweep-days 7` | start a new rolling pass this long after the last one finished |
| `--timeout 120` | wait longer for slow pages (default 120 s) |
| `--max-hours 6` | stop cleanly; next run continues |

### The classified register

The register is ordered the other way -- **oldest first**, new companies on
the last page -- so an update reads the tail for new companies. Existing
companies change in place (evaluations, certificate expiry, activities) and
can be removed, so each run also refreshes a slice of the register, sized
the same way as the rolling pass above (`--register-pages` caps it), and
starts a new refresh pass every `--register-days` (30). A run a month later
refreshes all 245 pages itself, about six minutes. A company missing from
two complete passes in a row
is marked `delisted_at` -- no longer classified, which matters for a
borrower that bids for government work. `companies --full` does a whole pass
in one go.

### Slow pages

Some pages take a long time to render: register page 2 (145 activity rows)
took 30.8 seconds with a cold cache, just over the original 30-second
timeout. A browser that shows it in five seconds is usually showing a cached
copy. Each request now waits up to 120 seconds, on a fresh connection for
the second try. A page that still
does not answer is **skipped and recorded**, the run carries on, and the next
run retries it. Five failures in a row stops the run: that pattern means the
site is struggling or refusing, and pressing on would make it worse.

`status` lists everything waiting to be retried.

## What to crawl, and how long it takes

At the default 1.5-second delay:

| Target | Requests | Roughly |
|---|---|---|
| `companies` (the classified register) | ~245 | **7 minutes** |
| `--kind awarded`, listings only | ~1,210 | 30 minutes |
| `--kind awarded` with detail pages | ~50,000 | 20 hours |
| `--kind awarded --no-details` | ~25,000 | 10 hours |
| `--kind all` | ~120,000 | several days |

Each tender costs up to two detail requests: `TenderDetails` (description,
required activity codes, ICV requirement, contract terms) and
`TenderCompaniesDetails` (who bid, who won). `--no-details` skips the first
and halves the crawl if you only care about who won what.

**Start with the register and one section.** Seven minutes plus an overnight
run tells you whether your borrowers appear at all, which is the question
that decides whether any of this is worth the full crawl.

Everything is resumable: the store records which pages are done per section,
and re-running picks up where it stopped.

---

## What it stores

One SQLite file.

| Table | Holds |
|---|---|
| `raw_page` | Every page as fetched, compressed, with its timestamp and a content hash that ignores the site's tracking script |
| `blob_dict` | One sample page per kind, used to compress the others (see below) |
| `tender` | Number, subject, ministry, family and state, all four dates, bond, document value, awarded amount, brief description, ICV requirement, contract duration, delivery location and the rest of the terms |
| `tender_activity` | The activity codes each tender requires |
| `company` | Per tender: winners and bidders, with name, CR number, value, financial result |
| `classified_company` | The register: profile number, type, CR, **size**, **government evaluation**, classification and its **expiry date** |
| `company_activity` | Each classified company's activity codes and **classification grade** |
| `sighting` | Every section each tender has been listed in: first and last seen, and when it left (`gone_at`) |
| `detail_fetch` | Which detail pages were read, at which state |
| `section_state` | Per section: first load, last full read, rolling pass position |
| `failed_page` | Pages waiting to be retried, and pages the site answers with its home page |
| `crawl_state` | Register pages read, for resume |
| `match` | Your customers joined to company rows, with method and score |
| `feature_cr_month` | CR-by-month features for modelling (`features`) |

**The raw HTML is kept on purpose.** Parsers get things wrong and sites change.
With the pages on disk you re-parse in seconds (`python -m monaqasat parse`)
instead of re-crawling 24,000 pages. It also means the fetch date of every
field is provable, which is what lets this be used as evidence in a credit
file rather than as an unsourced number.

### Keeping the file small

The pages are nearly all of the file, and every page on this site carries the
same navigation, scripts and styles. So one page of each kind is kept in
`blob_dict` as a sample, and the rest are compressed against it: measured on
the captured pages, a detail page stores about ten times smaller than its
HTML, and the whole set 13 times. Nothing is thrown away -- what comes back
out is the page exactly as fetched, and `parse` works as before.

Pages are compressed as they arrive. A database written before v0.5 keeps
its pages as they are until you run:

```powershell
python -m monaqasat compact          # compress what is stored, then VACUUM
```

It prints what it did and what the file went from and to. Two things worth
knowing: `VACUUM` needs room for a second copy of the file while it runs
(`--no-vacuum` skips it), and `--drop-before 2025-01-01` will also drop the
stored copy of older pages -- their parsed rows and content hashes stay, but
they can no longer be re-parsed without re-crawling. You rarely want that:
compression already makes the file small.

To move the data between machines, `python -m monaqasat pack` writes a copy
with no stored pages at all (about 20 MB, small enough for git). It leaves
out `raw_page`, `blob_dict` and `match` -- the last of those holds your
customers' ids and names, so a packed copy carries nothing of the bank's.

---

## Politeness and the firewall

`robots.txt` on this host is `User-agent: * / Disallow:` — nothing is
disallowed. The crawler re-reads it at startup and refuses to run if that ever
changes.

The site sits behind a web application firewall that returns HTTP 200 with a
"Request Rejected" body when it does not like the traffic. The fetcher detects
that page, backs off exponentially, and stops with a clear message rather than
hammering on. It uses one session with cookies, a browser User-Agent, a delay
with jitter between requests, and it makes no attempt to defeat the firewall —
if it keeps rejecting, that is the answer.

**Legal, before you run the full crawl.** robots.txt permits crawling, but the
site's Terms of Use contain a copyright clause: content must not be
"reproduced, republished, uploaded, sent, distributed or publicly displayed"
without the Ministry's written consent. Internal use in a credit assessment is
arguably none of those, but that is a judgement for QDB Legal, not for this
README. Get it in writing before award data appears in anything client-facing.

---

## Layout

```
monaqasat/
  fetch.py      polite session: TLS negotiation, robots check, delay,
                backoff, firewall-page detection
  diagnose.py   the doctor command
  parse.py      listing cards, TenderDetails, TenderCompaniesDetails and the
                classified register -> rows
  store.py      SQLite: raw HTML, tenders, companies, sightings, fetch log,
                failed pages, section progress, run history, matches
  match.py      CR -> exact name -> similar name -> transliteration, each
                match labelled with its method and score
  normalize.py  Qatari entity-name normalisation, Arabic-script folding,
                Arabic <-> Latin skeletons, CR numbers
  features.py   CR-by-month feature table
  cli.py        doctor | probe | harvest | crawl | companies | status |
                history | parse | match | analyse | features | stats |
                export | profile | pack | compact
  scripts/      harvest.ps1 and install-task.ps1 (unattended nightly run)
fixtures/       real markup captured from the live site, Sept 2026
tests/          offline tests -- a simulated site that publishes, moves and
                cancels tenders between runs (test_incremental,
                test_lifecycle), the register (test_register), upgrades of
                older databases (test_migration), matching (test_match)
```

```bash
python -m unittest discover -s tests -t .
```

The tests run against markup captured from the real site, so a change to
their HTML shows up as a test failure rather than as silently empty columns.

---

## Known limitations

- **All fourteen sections share one card layout**, confirmed against the live
  site; `awarded` and `bids-awarded` have been checked field by field. The
  others are the same shape with different labels, so check the first page of
  a new section before trusting a long crawl.
- **No ICV company data exists to scrape.** The Local Value (ICV) page is an
  explainer with a PowerPoint attached, not a register. The ICV signal you can
  actually get is per tender: `local_value_system` on the tender record (does
  this tender demand a Local Value certificate) and the `Local Value Ratio`
  column on the companies page, which is often blank.
- **Sub-contractors are invisible.** A borrower supplying a main contractor on
  a government job does not appear. Absence of awards is not absence of
  government work.
- **Names are transliterated inconsistently.** "Kyrwy Llmqawlat Walnqlyat
  Walkhdmlt" is a machine transliteration of an Arabic name, and some names
  appear in Arabic even on the English pages. Name matching handles all of
  it, but a name is not an identifier: two companies can share one. Use the
  CR; treat `name_fuzzy` and `name_translit` matches as leads to check.
- **No deduplication across branches.** CR numbers carry a branch suffix
  (`29309/1`); matching uses the root, so branches collapse onto the parent.
  Check that against your own CR conventions.
- **The bid-versus-win rate needs the opened sections.** `analyse` only sees
  what you crawled: without `technical` and `financial`, every company looks
  like it wins everything it enters. The rate is wins over *decided*
  tenders; bids still being evaluated are shown as open, not counted as
  losses.
- **Government evaluation is not explained anywhere public.** "Excellent",
  "Very Good", "Good" are the Ministry's own grades with no published
  methodology. Treat them as a reported fact, not a validated score.
