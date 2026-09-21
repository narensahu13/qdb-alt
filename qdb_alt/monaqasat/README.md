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

The CR number is what makes matching reliable. Supply it wherever you have it.

Arabic names are transliterated automatically. If the customer list has
`name_ar` (or the name column itself is Arabic) it is converted to Latin and
compared to Monaqasat's English names — CR first, then exact key, then fuzzy,
including machine transliterations such as "Kyrwy Llmqawlat". No extra step.

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
`status` shows every section complete. After that, the same task is a cheap
incremental update: new tenders, and any tender that moved (open last month,
awarded this month) is refetched and the stored status is updated in place.

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
python -m monaqasat crawl --kind companies         # the six sections that name companies
python -m monaqasat companies                      # new classified companies
python -m monaqasat status                         # what's done, what's pending
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

1. **Catch up.** Reads the newest listing pages until two in a row contain
   nothing new, then stops.
2. **Finish an interrupted first load.** If a previous run never reached the
   last page, it carries on from where it stopped -- allowing for the drift
   since then -- rather than starting over.
3. **Fill gaps.** Any listing page that failed before is re-read, in the
   neighbourhood it has drifted to since.
4. **Details.** Fetches detail pages only for tenders that are new, or whose
   state has moved on. When a tender goes from technically opened to awarded,
   its companies page is fetched again -- that is where the winners appear --
   but its description is not, because that does not change.

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
| `--full` | read every listing page instead of stopping when caught up. Still only fetches details that are missing or stale. Use occasionally to sweep for anything the incremental pass could miss. |
| `--pages 1-100` | stay within that range |
| `--limit-details 500` | cap detail requests this run -- spread a large first load over several days |
| `--stop-after 3` | be more cautious about deciding it has caught up |
| `--timeout 120` | wait longer for slow pages (default 90 s) |
| `--max-hours 6` | stop cleanly; next run continues |

### The classified register

The register is ordered the other way -- **oldest first**, new companies on
the last page -- so an update only reads the tail. But existing companies
change in place: evaluations, certificate expiry, activities. Refresh them
monthly with `python -m monaqasat companies --full`, which takes about seven
minutes.

### Slow pages

Some pages take over a minute to render, in a browser as well -- register
page 2 is one. Each request waits up to 90 seconds, twice. A page that still
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
| `raw_page` | Every page's HTML exactly as fetched, with its timestamp |
| `tender` | Number, subject, ministry, family and state, all four dates, bond, document value, awarded amount, brief description, ICV requirement, contract duration, delivery location and the rest of the terms |
| `tender_activity` | The activity codes each tender requires |
| `company` | Per tender: winners and bidders, with name, CR number, value, financial result |
| `classified_company` | The register: profile number, type, CR, **size**, **government evaluation**, classification and its **expiry date** |
| `company_activity` | Each classified company's activity codes and **classification grade** |
| `crawl_state` | Crawl position per section, for resume |
| `match` | Your customers joined to company rows, with method and score |

**The raw HTML is kept on purpose.** Parsers get things wrong and sites change.
With the pages on disk you re-parse in seconds (`python -m monaqasat parse`)
instead of re-crawling 24,000 pages. It also means the fetch date of every
field is provable, which is what lets this be used as evidence in a credit
file rather than as an unsourced number.

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
  match.py      CR number -> exact name -> fuzzy name, in that order;
                Arabic customer names are transliterated automatically
  normalize.py  Qatari entity-name normalisation + Arabic → Latin
  cli.py        doctor | probe | harvest | crawl | companies | status |
                parse | match | analyse | stats | export | profile
  scripts/      harvest.ps1 and install-task.ps1 (unattended nightly run)
fixtures/       real markup captured from the live site, Sept 2026
tests/          offline tests -- including a simulated site that publishes
                tenders between runs, and Arabic name matching
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
  Walkhdmlt" is a machine transliteration of an Arabic name. Matching
  transliterates Arabic customer names (and a `name_ar` column) and also
  falls back to a consonant skeleton, so CR is still best but names join
  without a manual mapping file.
- **No deduplication across branches.** CR numbers carry a branch suffix
  (`29309/1`); matching uses the root, so branches collapse onto the parent.
  Check that against your own CR conventions.
- **The bid-versus-win rate needs the opened sections.** `analyse` only sees
  what you crawled: without `technical` and `financial`, every company looks
  like it wins everything it enters.
- **Government evaluation is not explained anywhere public.** "Excellent",
  "Very Good", "Good" are the Ministry's own grades with no published
  methodology. Treat them as a reported fact, not a validated score.
