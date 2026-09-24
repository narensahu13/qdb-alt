# ICV — QatarEnergy's In-Country Value register

The second alternate-data source in this repository, beside
[`monaqasat`](../monaqasat). Monaqasat says what a company won from the state;
ICV says how competitive it is for that work in the first place. They join on
the CR number.

QatarEnergy publishes an ICV score for every certified supplier — broadly, how
much of what the company sells is made in Qatar. Operating companies weight
tenders by it, so for a borrower in that supply chain the score is close to a
licence to bid, and its movement leads the revenue we are lending against.

This mirrors the register locally, with every company's **published score
history**, so a borrower's score can be read *as at a date* rather than only as
at today.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` matters on a QDB machine: the network terminates TLS at a proxy
whose root is in the Windows certificate store but not in the one `requests`
ships with. Without it you get a certificate error that looks like the site is
down.

## First, check the machine can read the portal

```powershell
python -m icv doctor
```

Five steps, each named. If one fails it says which, which is the difference
between "the portal changed" and "this laptop is behind a proxy".

## Loading it

```powershell
python -m icv crawl                      # everything: ~2 requests per company
python -m icv crawl --limit 500          # or a batch at a time
python -m icv stats
```

The first load is about **7 requests for the whole supplier list** and then one
page plus one call for each of ~6,900 companies — roughly **six hours** at the
polite 1.5 s delay. It is resumable: stop it whenever, and the next run picks
up the companies whose history has not been read.

Afterwards, once a month:

```powershell
python -m icv crawl
```

The list sweep is a minute. Only companies whose score, status, certificate or
expiry moved get their history re-read — usually a few hundred — so a monthly
run is minutes, not hours.

## Matching the customer book

```powershell
python -m icv match --customers ..\..\customer-data\customers.csv --out icv-matches.csv
```

CR number first, then exact name, then fuzzy, then Arabic/Latin
transliteration — the same ladder as Monaqasat, and every match records which
method produced it so CR joins can be used as they are and the rest reviewed.

**Keep the real book out of this repository.** `customer-data\` and
`*.book.csv` are ignored; `sample/customers.csv` in the Monaqasat package is a
five-row example. One borrower often needs several rows — a changed
registration, a subsidiary, a QFC entity — under one customer id. Every row is
matched and the results are filed under that id.

## The live fallback

For an applicant who is not in the mirror yet:

```powershell
python -m icv refresh --cr 185319
```

One company, fetched now, stored like any other. If the portal has nothing,
that is itself an answer: the company is not a certified supplier today.

## What is in the database

| Table | What it holds |
|---|---|
| `company` | one row per supplier, keyed by the portal's own GUID |
| `score_observation` | every published change of score: date, score, reason, delta |
| `listing_state` | status, certificate and expiry, as periods with a start and an end |
| `company_service`, `company_good` | what the supplier sells (only with `--goods`) |
| `match` | customer ↔ supplier. Never leaves this file |
| `run`, `raw_page`, `crawl_state` | how it got here |

Reading it as at a date:

```sql
SELECT score, posted_date, reason FROM score_observation
 WHERE company_id = :id AND posted_date <= :asof
 ORDER BY posted_date DESC LIMIT 1;
```

`python -m icv pack` writes a copy without `raw_page` or `match`, so a database
can be shared without customer identities. A test fails if that ever stops
being true.

## Four things that will bite otherwise

**Score history is retrospective. Status history is not.** The portal publishes
every past score change, so one backfill gets the whole panel. Status, grace
period and expiry appear *only* in the listing, so `listing_state` starts the
day of the first sweep and can never be recovered for the past. A certificate
quietly lapsing is a real signal about a borrower — and it only exists if you
were watching. Start the monthly sweep before the modelling, not after.

**Not every rise is an improvement.** The reason column distinguishes
`Certificate Renewed` from `Transitional Percentage Added` — and one company's
+7.00 in November 2024 was a policy change, not better local content. Feed raw
deltas into a PD model and it will learn a regulation. `stats` groups
observations by reason so this stays visible.

**The headline score is capped.** It is
`min(100, icv + manufacturer + bonus + transitional)`, so a company at 100.00
may be well past it underneath. The top of the range is censored; treat it as
such.

**Compare against the cohort, not a threshold.** 24% means nothing on its own;
24% against a Manufacturing median of 38% means something.
`Store.industry_scores_as_at` gives the cohort at the same date.

## Notes for anyone changing the crawler

The portal is Microsoft Power Pages over Dataverse, so the lists are JSON, not
HTML. Three measured facts drive the code, each with a test:

- **`ItemCount` lies.** The guest list reports 5,000 however many there are; an
  exhaustive sweep on 24 Sep 2026 found **6,892**. Only `MoreRecords` may be
  believed — and asking past the last page serves the last page again, so the
  sweep also stops when a page brings nothing new.
- **`NextPagePagingCookie` comes back empty** on the guest list, so paging is by
  page number. At `pageSize 1000` that returned no duplicates over the whole
  register.
- **A company's history can only be fetched after fetching that company's
  page.** The signed `base64SecureConfiguration` carries the identity; passing
  a different `entityId` beside it is ignored and you get the first company's
  history back. That was measured, and it is why the backfill costs two
  requests per company rather than one.

`normalize.py` and `match.py` are copies of the Monaqasat ones, with `cr_root`
extended for the `QFC/00326` form — QFC registrations run in their own sequence,
so `QFC/00326` and CR `326` are unrelated companies. Keep the copies in step, or
move the shared part into a package of its own and delete both.

```powershell
python -m unittest discover -s tests -t .    # 70 tests, offline
```

The tests run against a stand-in portal that reproduces the three behaviours
above, because this repository's build environment cannot reach
`icv.tawteen.com.qa`. `fixtures/README.md` says exactly what was captured from
the live site and what was reduced.
