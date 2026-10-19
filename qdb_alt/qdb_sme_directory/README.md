# QDB SME Directory 2024

A third alternate-data source beside [`monaqasat`](../monaqasat) and
[`icv`](../icv): the company profiles QDB itself published in the
**SME Directory 2024** (May 2024), one PDF for manufacturing and one for
services. A profile says what the company makes, under which HS codes, whether
it exports, its website and often the year it started.

For a borrower this is a cheap, official answer to "is this a real, operating
manufacturer, and since when" — and being in QDB's own directory is a signal
in itself.

## Install

```powershell
pip install -r requirements.txt
```

`truststore` is there for the same reason as in `icv`: behind QDB's TLS proxy
`requests` needs the Windows certificate store.

## Loading it

```powershell
python -m qdb_sme_directory load                      # manufacturing edition
python -m qdb_sme_directory load --edition all        # manufacturing and services
python -m qdb_sme_directory stats
```

The first `load` downloads the PDF (~135 MB, into `data/`, which git ignores)
and reads it in a few seconds; later loads re-read the local copy. The
directory is a one-off publication, so there is nothing to keep current until
QDB publishes the next edition.

## Matching the customer book

```powershell
python -m qdb_sme_directory match customers.csv --out qdb_sme_directory_features.csv
```

The customer file is the same as for the other sources: `customer_id`, `name`,
`cr_number` (and optionally `search_name`, the trading name to search for when
the registered name differs). One row per customer comes out:

| column | meaning |
|---|---|
| `qdb_dir_listed` | 1 if the company has a profile, 0 if not |
| `qdb_dir_edition` | manufacturing / services |
| `qdb_dir_exporter` | 1 if the profile carries the *Exporter* mark |
| `qdb_dir_hs_codes` | number of HS product codes in the profile |
| `qdb_dir_founded_year` | founding year, when the description states it |
| `qdb_dir_website` | the website printed in the profile |
| `matched_profile`, `match_score`, `match_method` | what was matched, for a person to check |

Empty means unknown, not zero: a company that is not in the directory has
`qdb_dir_listed = 0` and every other column empty.

### How the name match works

The directory has no CR numbers, so the join is by name, and it is strict —
a missed company is a blank feature, a wrong company is a wrong one. Every
distinctive word of the customer's name must be in the profile name, and the
first one (the brand) must be the same word: *Al Mana Switchgear* is not
*Al Dana Switchgear*, *Floral Detergent* is not *Global Detergent*,
*National Plastic* is not *International Plastic*. Spelling variants of the
same Arabic name are allowed (*Hudaifi / Hodaifi*, *Rahalat / Rehlat*), as are
plurals, glued words (*Westbay*) and abbreviations (*Extra Co / Extraco*).
The rules and the real cases behind them are in `namematch.py` and
`tests/test_namematch.py`.

Names wrapped at 40 characters by the system the customer list came from
("PLASTIC PROD UCTS") are rejoined before searching.

## How a profile is read

Each profile is one page. The text layer interleaves English and Arabic, so
fields are read by pattern: the English name from the table of contents (or,
failing that, the Latin lines above the *Product Names* marker), the website,
the *Exporter* mark, the HS codes, the product bullets, and the founding year
from phrases like "operational since 2019" or "established on May 3, 2011".
Contact people and phone numbers are on the page; they are not stored.

## The run log

Every command is recorded in the `run` table: when it started and finished,
how long it took, how many rows it wrote, a note ("82 of 319 customers
found") and, if it failed, the error.

```powershell
python -m qdb_sme_directory runs
```

## Tests

```powershell
python -m unittest discover -s tests
```

They run offline on `fixtures/directory_sample.txt`, a made-up directory in
the real layout.

## Known limits

- The directory is dated May 2024: a company that opened later, or did not
  apply, is not in it. `qdb_dir_listed = 0` means "not in this directory",
  not "not a manufacturer".
- The *Exporter* mark is read from the page text; on a few pages the text
  layer places it next to the wrong column. Treat it as a hint.
- On the 319-company client list used to build this, 82 were found in the
  manufacturing edition; the services edition has not been run against it.
